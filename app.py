#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票识别汇总工具 - 主程序（Tkinter GUI）

选择文件夹 -> 递归扫描它和它的全部子文件夹 -> 每个含票据的目录各自走一遍：
[前置] 有 PDF 则逐页转 JPG 到该目录的「处理后」-> OCR 识别该目录全部图片
-> 识别失败的图复制到该目录的 未识别/ -> 在该目录导出 发票识别汇总.xlsx

识别过程中可随时点「停止」：当前图片处理完即收工，已识别的结果与已生成的 Excel 全部保留，
下次点「开始识别」会接着处理剩余的图片（PDF 转图、复制均为幂等）。

界面：品牌头 + 圆角卡片 + 实时统计卡（票据/成功/未识别/价税合计），
自绘按钮 / 复选框 / 进度条。

PDF 前置步骤见 pdf_convert.py；目录递归收集见 pdf_convert.collect_dirs。

⚠️ ui_kit 必须最先 import：它在 import tkinter 之前开启 DPI 感知（高分屏不发虚）。
"""
import ui_kit as K          # noqa: E402  ⚠️ 必须先于本文件其余任何 tkinter 接触

import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import autoupdate
from excel_out import export_excel
from parser import IMG_EXTS, parse_image
from pdf_convert import (WORK_DIR, collect_dirs, prepare, summary as prep_summary)

VERSION = "1.4.0"
APP_TITLE = f"发票识别汇总工具 v{VERSION}"
OUT_XLSX = "发票识别汇总.xlsx"
UNKNOWN_DIR = "未识别"
# 总结里最多列几个目录的 Excel 路径（超出的用一行概括，免得弹窗长到看不完）
MAX_LIST_DIRS = 10

# 皮肤：与旧版标题色同源的商务蓝
SKIN = K.Skin("ocr", "精致卡片", "品牌头 + 统计卡",
              accent="#1F4E79", accent_d="#163A5F", accent_l="#EAF1F8",
              radius_card=10, radius_btn=9, shadow=True, btn_h=42,
              header="flat", option_style="check", picker_style="entry")

COLS = ("file", "no", "date", "buyer", "seller", "nitems", "total", "state")
HEADS = ("文件名", "发票号码", "开票日期", "购买方", "销售方", "项目数",
         "价税合计", "状态")
WIDTHS = (260, 165, 85, 110, 180, 55, 90, 70)
STATS = (("tickets", "票据总数"), ("ok", "识别成功"),
         ("fail", "未识别"), ("total", "价税合计"))
# 统计卡左侧色条：蓝 / 绿 / 红 / 深蓝
STAT_COLORS = {"tickets": "#1F4E79", "ok": "#1E8E5A",
               "fail": "#C0392B", "total": "#163A5F"}


def dir_label(rel):
    """目录展示名：根目录 →「根目录」，子目录 → 相对路径。"""
    return "根目录" if rel in (".", "") else rel


def archive_unknown(work, recs):
    """把本目录里未识别的图复制一份到 work/未识别（原图不动）。返回复制张数。"""
    unknown = [r for r in recs if not r["ok"]]
    if not unknown:
        return 0
    udir = os.path.join(work, UNKNOWN_DIR)
    try:
        os.makedirs(udir, exist_ok=True)
    except OSError:
        return 0
    n = 0
    for r in unknown:
        src = os.path.join(work, r["file"])
        dst = os.path.join(udir, r["file"])
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                n += 1
            except OSError:
                pass
    return n


def export_dir(work, recs):
    """导出单个目录的 Excel（只含识别成功的行，与旧版口径一致）。"""
    return export_excel([r for r in recs if r["ok"]],
                        os.path.join(work, OUT_XLSX))


class App:
    def __init__(self, root):
        self.root = root
        self.sk = SKIN
        K.setup_scale(root)
        root.title(APP_TITLE)
        # ⚠️ DPI 感知下 geometry/minsize 用物理像素，逻辑尺寸必须过 u()：
        #    直接写 1080 会得到「物理 1080px」的小窗，内容按逻辑点请求放不下，
        #    统计卡会被挤掉最后一张、表格列全被压缩。
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(1080, sw - 40)
        h = min(720, sh - 60)
        root.geometry("%dx%d" % (K.u(w), K.u(h)))
        root.minsize(K.u(920), K.u(600))
        root.configure(bg=self.sk.bg)

        # 运行状态（先于 UI 建）
        self.q = queue.Queue()
        self.records = []            # 本次运行识别到的全部行（跨目录累计）
        self.dir_results = []        # 每个目录一条：{rel, work, prep, recs, copied, excel}
        self.folder = tk.StringVar()
        self.root_dir = ""           # 用户选中的根目录
        self.work_dir = None         # 单个目录时=该目录的工作目录；多目录时=根目录
        self.cur_dir = "."           # 正在处理的目录（相对根目录）
        self.eng = None              # OCR 引擎，跨目录复用（加载一次很贵）
        self.running = False
        self._stop = threading.Event()
        self._closing = False
        self._poll_job = None
        self._post_job = None
        self._indet_job = None
        self._indet = False
        self._indet_v = 0.0

        self._build_style()
        self._build_ui()
        root.bind("<Destroy>", self._on_root_destroy)
        self._poll()
        # 开窗之后再动更新的事，不拖慢启动
        self._post_job = root.after(600, self._post_start)

    # ---------- 自动更新 ----------
    def _ulog(self, m):
        """更新模块的日志回调：只写状态栏，不弹窗（静默检查失败不打扰用户）。"""
        try:
            self.lbl_out.configure(text=str(m)[:64])
        except Exception:
            pass

    def _before_install(self):
        """新版已就位、本进程即将退出前调用：停掉后台任务，确保能退干净。"""
        self.running = False

    def _post_start(self):
        self._post_job = None
        # 上次自动更新若留下中间文件，启动时接管文件名并清理
        try:
            autoupdate.settle_after_update(log_fn=self._ulog)
        except Exception:
            pass
        self._check_update(manual=False)

    def _check_update(self, manual):
        autoupdate.run_update_check(
            self.root, app_name=autoupdate.APP_NAME, current_version=VERSION,
            config_file=autoupdate.CONFIG_FILE, log_fn=self._ulog,
            on_before_install=self._before_install, manual=manual)

    def check_update(self):
        self._check_update(manual=True)

    # ---------- UI ----------
    def _build_style(self):
        sk = self.sk
        K.style_ttk(sk)                      # clam + P.TEntry / P.Vertical.TScrollbar
        st = ttk.Style()
        st.configure("Treeview", background=sk.card, fieldbackground=sk.card,
                     foreground=sk.text, font=K.f(9), rowheight=K.u(28),
                     borderwidth=0, relief="flat")
        st.configure("Treeview.Heading", font=K.f(9, True), background="#F1F4FA",
                     foreground=sk.text, relief="flat", borderwidth=0,
                     padding=(K.u(6), K.u(6)))
        st.map("Treeview.Heading", background=[("active", "#E7ECF5")])
        st.map("Treeview", background=[("selected", sk.accent_l)],
               foreground=[("selected", sk.accent_d)])

    def _btn(self, master, text, command, kind="primary", h=None, size=12):
        """自适应宽度的圆角按钮：实测文字宽（物理px）→ 换算逻辑值 + 36 内边距。"""
        fnt = tkfont.Font(family=K.FONT_UI, size=size, weight="bold")
        w = int(fnt.measure(text) / K.SCALE) + 36
        return K.RoundButton(master, self.sk, text=text, command=command,
                             kind=kind, height=h, width=w, font_size=size)

    def _build_ui(self):
        sk = self.sk
        root = self.root

        # ── 品牌头（flat：浅渐变底 + logo + 标题/副标题 + 版本徽章） ──
        # ⚠️ 标题 15pt 在 150% DPI 下字形下沉明显，副标题 y 必须 ≥ 标题底 + 空隙，
        #    否则两行视觉上贴死甚至重叠（v1.3.2 就栽在这）。
        head = tk.Canvas(root, bg=sk.bg, highlightthickness=0, bd=0,
                         height=K.u(72))
        head.pack(fill="x")

        def _paint_head(cv, w, h):
            cv.delete("all")
            if w < 40 or h < 20:
                return
            K.h_gradient(cv, 0, 0, w, h, "#E9F1F9", "#F4F7FB", steps=72)
            K.logo_mark(cv, K.u(20), K.u(16), K.u(40), sk)
            cv.create_text(K.u(74), K.u(13), text="发票识别汇总",
                           font=K.f(15, True), fill=sk.text, anchor="nw")
            cv.create_text(K.u(76), K.u(47), anchor="w",
                           text="本地离线 OCR · 自动汇总 Excel · 数据不上传",
                           font=K.f(9), fill=sk.muted)
            # 右侧版本徽章
            t = "v" + VERSION
            tw = K.text_w(cv, t, K.f(9, True))
            bx1, bx2 = w - K.u(24) - tw - K.u(18), w - K.u(24)
            by1, by2 = (h - K.u(24)) / 2, (h + K.u(24)) / 2
            K.rr(cv, bx1, by1, bx2, by2, K.u(12), sk.accent_l)
            cv.create_text((bx1 + bx2) / 2, (by1 + by2) / 2, text=t,
                           font=K.f(9, True), fill=sk.accent_d)

        head.bind("<Configure>", lambda e: _paint_head(head, e.width, e.height))

        # ── 文件夹卡 ──
        c_folder = K.Card(root, sk, pad=14)
        c_folder.pack(fill="x", padx=K.u(16))
        row1 = tk.Frame(c_folder.body, bg=sk.card)
        row1.pack(fill="x")
        tk.Label(row1, text="票据文件夹", font=K.f(9), fg=sk.muted,
                 bg=sk.card).pack(side="left")
        self.ent = ttk.Entry(row1, style="P.TEntry", textvariable=self.folder)
        self.ent.pack(side="left", fill="x", expand=True, padx=K.u(8))
        self.btn_browse = self._btn(row1, "浏览…", self.pick_folder,
                                    kind="ghost", h=36)
        self.btn_browse.pack(side="left", padx=(0, K.u(8)))
        self.btn_run = self._btn(row1, "开始识别", self.start, kind="primary", h=36)
        self.btn_run.pack(side="left")
        self.btn_stop = self._btn(row1, "停止", self.stop, kind="danger", h=36)
        self.btn_stop.pack(side="left", padx=(K.u(8), 0))
        self.btn_stop.configure_state("disabled")

        row2 = tk.Frame(c_folder.body, bg=sk.card)
        row2.pack(fill="x", pady=(K.u(10), 0))
        self.var_pdf = tk.BooleanVar(value=True)
        chk = K.RoundCheck(row2, sk, variable=self.var_pdf,
                           text="自动把 PDF 转成图片（PDF 逐页转 JPG，连同原有图片"
                                "一起转入「%s」文件夹后再识别）" % WORK_DIR)
        chk.pack(fill="x", anchor="w")   # fill 拉伸至 body 宽，长文案不被裁

        tk.Label(c_folder.body, bg=sk.card, fg=sk.faint, font=K.f(9), anchor="w",
                 text="自动递归处理所选文件夹及其全部子文件夹：每个含票据的目录"
                      "各自识别、各自生成 Excel 与「未识别」，识别中可随时「停止」"
                 ).pack(fill="x", pady=(K.u(6), 0))

        # ── 统计卡行（4 张；⚠️ 必须显式给宽，否则互相抢宽被挤丢） ──
        srow = tk.Frame(root, bg=sk.bg)
        srow.pack(fill="x", padx=K.u(16), pady=(K.u(10), 0))
        self.stat_labels = {}
        for key, cap in STATS:
            c = K.Card(srow, sk, pad=12)
            c.configure(width=K.u(220))
            c.pack(side="left", expand=True, fill="x", padx=(0, K.u(10)))
            top = tk.Frame(c.body, bg=sk.card)
            top.pack(fill="x")
            bar = tk.Frame(top, bg=STAT_COLORS.get(key, sk.accent),
                           width=K.u(4), height=K.u(17))
            bar.pack(side="left", padx=(0, K.u(8)))
            lab = tk.Label(top, text="—", font=K.f(15, True),
                           fg=sk.accent_d, bg=sk.card)
            lab.pack(side="left")
            tk.Label(c.body, text=cap, font=K.f(9), fg=sk.muted,
                     bg=sk.card).pack(anchor="w")
            self.stat_labels[key] = lab

        # ── 进度卡 ──
        c_prog = K.Card(root, sk, pad=12)
        c_prog.pack(fill="x", padx=K.u(16), pady=(K.u(10), 0))
        self.progress = K.RoundProgress(c_prog.body, sk, height=10)
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_stat = tk.Label(c_prog.body, text="就绪", font=K.f(9),
                                 fg=sk.muted, bg=sk.card, width=40, anchor="e")
        self.lbl_stat.pack(side="left", padx=(K.u(10), 0))

        # ── 结果表格卡（高度随窗口拉伸） ──
        c_tbl = K.Card(root, sk, pad=10, fill=True)
        c_tbl.pack(fill="both", expand=True, padx=K.u(16), pady=(K.u(10), 0))
        tbl = c_tbl.body
        self.tree = ttk.Treeview(tbl, columns=COLS, show="headings")
        for c, h2, w in zip(COLS, HEADS, WIDTHS):
            self.tree.heading(c, text=h2)
            self.tree.column(c, width=K.u(w), anchor="w" if c in ("file", "buyer", "seller")
                             else "center", stretch=(c in ("file", "buyer", "seller")))
        vsb = ttk.Scrollbar(tbl, orient="vertical", style="P.Vertical.TScrollbar",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("fail", foreground="#C00000")
        self.tree.tag_configure("odd", background="#F6F9FC")

        # ── 底栏 ──
        bottom = tk.Frame(root, bg=sk.bg)
        bottom.pack(fill="x", padx=K.u(16), pady=(K.u(8), K.u(12)))
        self.btn_open = self._btn(bottom, "打开所在文件夹", self.open_folder,
                                  kind="ghost", h=30, size=9)
        self.btn_open.pack(side="left")
        self.btn_open.configure_state("disabled")
        tk.Label(bottom, text="v" + VERSION, font=K.f(9), fg=sk.faint,
                 bg=sk.bg).pack(side="left", padx=(K.u(10), 0))
        self.lbl_out = tk.Label(bottom, text="", font=K.f(9), fg=sk.muted, bg=sk.bg)
        self.lbl_out.pack(side="left", padx=K.u(10))
        self.btn_update = self._btn(bottom, "检查更新", self.check_update,
                                    kind="ghost", h=30, size=9)
        self.btn_update.pack(side="right")
        self.btn_export = self._btn(bottom, "重新导出 Excel", self.export,
                                    kind="ghost", h=30, size=9)
        self.btn_export.pack(side="right", padx=(0, K.u(8)))
        self.btn_export.configure_state("disabled")

    # ---------- 统计卡 ----------
    def _update_stats(self):
        n = len(self.records)
        ok = sum(1 for r in self.records if r["ok"])
        fail = n - ok
        total = sum(r["total"] for r in self.records
                    if r["ok"] and r["total"] is not None)
        dash = "—" if not n else None
        vals = {"tickets": dash or str(n),
                "ok": dash or str(ok),
                "fail": dash or str(fail),
                "total": dash or ("¥" + format(total, ",.2f"))}
        for key, lab in self.stat_labels.items():
            col = self.sk.accent_d
            if n and key == "fail" and fail:
                col = self.sk.bad
            elif n and key == "ok" and not fail:
                col = self.sk.ok
            lab.configure(text=vals[key], fg=col)

    # ---------- 进度（准备阶段流动模拟） ----------
    def _start_indet(self):
        self._stop_indet()          # 防御：上次流动链未停时避免双链并行
        self._indet = True
        self._indet_v = 4.0
        self._tick_indet()

    def _tick_indet(self):
        if not self._indet or self._closing:
            return
        self._indet_v = min(92.0, self._indet_v + max(0.5, (92.0 - self._indet_v) * 0.02))
        self.progress.set_value(self._indet_v)
        self._indet_job = self.root.after(120, self._tick_indet)

    def _stop_indet(self):
        self._indet = False
        if self._indet_job:
            try:
                self.root.after_cancel(self._indet_job)
            except Exception:
                pass
            self._indet_job = None

    # ---------- 句柄清理 ----------
    def _stop_polling(self):
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

    def _on_root_destroy(self, e):
        if e.widget is not self.root:
            return
        self._closing = True
        self._stop_polling()
        self._stop_indet()
        if self._post_job:
            try:
                self.root.after_cancel(self._post_job)
            except Exception:
                pass
            self._post_job = None

    # ---------- actions ----------
    def pick_folder(self):
        d = filedialog.askdirectory(title="选择票据所在文件夹（图片 / PDF）")
        if d:
            self.folder.set(d)

    def open_folder(self):
        d = self.work_dir or self.folder.get()
        if d and os.path.isdir(d):
            os.startfile(d)

    def start(self):
        if self.running:
            return
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(APP_TITLE, "请先选择有效的文件夹")
            return
        use_pdf = bool(self.var_pdf.get())
        self.running = True
        self.records = []
        self.dir_results = []
        self.root_dir = folder
        self.work_dir = None
        self.cur_dir = "."
        self._stop.clear()
        self.btn_run.configure_state("disabled", "识别中…")
        self.btn_stop.configure_state("normal", "停止")
        self.btn_export.configure_state("disabled")
        self.btn_open.configure_state("disabled")
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._update_stats()
        self.lbl_out.configure(text="")
        self._start_indet()
        self.lbl_stat.configure(text="扫描子文件夹…")
        threading.Thread(target=self._worker, args=(folder, use_pdf),
                         daemon=True).start()

    def stop(self):
        """请求停止：立标志，识别线程在下一个检查点（每张图 / 每个目录 / 每页 PDF）收工。"""
        if not self.running:
            return
        self._stop.set()
        self.btn_stop.configure_state("disabled", "正在停止…")
        self.lbl_stat.configure(text="正在停止…（当前图片处理完即停）")

    def _worker(self, folder, use_pdf):
        # ⚠️ 整体兜底：识别线程里任何未捕获异常（OCR 引擎加载失败、目录被删、
        #    磁盘错误…）若不回报，主界面会永远停在流动进度上，用户只能强杀进程。
        try:
            all_dirs = collect_dirs(folder)
            targets = [d for d in all_dirs if d["imgs"] or (use_pdf and d["pdfs"])]
            if not all_dirs:
                self.q.put(("warn", "该文件夹及其子文件夹里都没有找到图片或 PDF 文件。"))
                return
            if not targets:
                self.q.put(("warn", "找到的 %d 个文件夹里只有 PDF，请勾选下面的"
                                    "「自动把 PDF 转成图片」后再试。" % len(all_dirs)))
                return
            self.q.put(("dirs", len(targets)))
            eng = None
            done_n = 0
            for di, d in enumerate(targets, 1):
                if self._stop.is_set():
                    break
                rel, path = d["rel"], d["path"]
                self.q.put(("dir", di, len(targets), rel))
                prep = prepare(path, enabled=use_pdf, cancel=self._stop.is_set,
                               log=lambda m: self.q.put(("stage", m)))
                work = prep["work_dir"]
                try:
                    imgs = [f for f in sorted(os.listdir(work))
                            if os.path.splitext(f)[1].lower() in IMG_EXTS
                            and not f.startswith("_")]
                except OSError:
                    imgs = []
                self.q.put(("prep", prep, len(imgs)))
                recs = []
                if imgs and not self._stop.is_set():
                    if eng is None:
                        self.q.put(("stage", "加载识别引擎…"))
                        from rapidocr_onnxruntime import RapidOCR
                        eng = RapidOCR()
                        self.eng = eng
                    for i, name in enumerate(imgs, 1):
                        if self._stop.is_set():
                            break
                        rec = parse_image(eng, os.path.join(work, name))
                        recs.append(rec)
                        self.q.put(("row", rel, rec))
                        self.q.put(("progress", di, len(targets), i, len(imgs), name))
                stopped = self._stop.is_set()
                copied = archive_unknown(work, recs)
                info = None
                if recs:
                    try:
                        info = export_dir(work, recs)
                    except Exception as e:  # noqa: BLE001
                        self.q.put(("stage", "导出 Excel 失败：%s" % e))
                done_n += 1
                self.q.put(("dir_done", {"rel": rel, "work": work, "prep": prep,
                                         "recs": recs, "copied": copied,
                                         "excel": info}))
                if stopped:
                    break
            self.q.put(("stopped" if self._stop.is_set() else "all_done",
                        done_n, len(targets)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", "%s" % e))

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "row":
                    rel, rec = msg[1], msg[2]
                    self.records.append(rec)
                    # 表格里带上相对目录，跨子文件夹时不会张冠李戴（Excel 里仍是纯文件名）
                    disp = rec["file"] if rel in (".", "") else "%s\\%s" % (rel, rec["file"])
                    total = f"{rec['total']:,.2f}" if rec["total"] is not None else "—"
                    tags = []
                    if not rec["ok"]:
                        tags.append("fail")
                    elif len(self.records) % 2 == 0:
                        tags.append("odd")      # 斑马纹（失败红字优先）
                    self.tree.insert("", "end", tags=tuple(tags),
                                     values=(disp, rec["invoice_no"] or "—",
                                             rec["date"] or "—", rec["buyer"] or "—",
                                             rec["seller"] or "—",
                                             len(rec["items"]) or "—", total,
                                             "成功" if rec["ok"] else "未识别"))
                    self._update_stats()
                elif kind == "stage":
                    self.lbl_stat.configure(text=str(msg[1])[:40])
                elif kind == "dirs":
                    self.lbl_out.configure(text="共 %d 个文件夹待识别" % msg[1])
                elif kind == "dir":
                    di, n, rel = msg[1], msg[2], msg[3]
                    self.cur_dir = rel
                    self.lbl_stat.configure(
                        text=("[%d/%d] %s" % (di, n, dir_label(rel)))[:40])
                elif kind == "prep":
                    prep, n = msg[1], msg[2]
                    self._stop_indet()
                    self.progress.set_value(0)
                    if prep["new_dir"]:
                        self.lbl_stat.configure(
                            text="PDF 已转 %d 页，开始识别…" % (prep["rendered"] + prep["reused"]))
                    else:
                        self.lbl_stat.configure(text="0/%d" % n)
                elif kind == "progress":
                    di, dn, i, n, name = msg[1], msg[2], msg[3], msg[4], msg[5]
                    self.progress.set_value(i * 100.0 / max(n, 1))
                    self.lbl_stat.configure(
                        text=("[%d/%d] " % (di, dn) + f"{i}/{n}  {name[:22]}")[:40])
                elif kind == "dir_done":
                    self.dir_results.append(msg[1])
                elif kind == "all_done":
                    self._finish(msg[1], msg[2], stopped=False)
                elif kind == "stopped":
                    self._finish(msg[1], msg[2], stopped=True)
                elif kind == "warn":
                    self._recover(msg[1], soft=True)
                elif kind == "error":
                    self._recover(msg[1])
        except queue.Empty:
            pass
        self._poll_job = self.root.after(120, self._poll)

    def _recover(self, reason, soft=False):
        """线程报错 / 没有票据可识别时恢复界面（对应 _poll 的 error、warn 分支）。"""
        self.running = False
        self._stop_indet()
        self.btn_run.configure_state("normal", "开始识别")
        self.btn_stop.configure_state("disabled", "停止")
        self.btn_export.configure_state("normal" if self.dir_results else "disabled")
        self.progress.set_value(0, stopped=True)
        self.lbl_stat.configure(text=str(reason)[:40])
        self._update_stats()
        if soft:
            messagebox.showinfo(APP_TITLE, reason)
        else:
            messagebox.showerror(APP_TITLE, "识别过程出错：%s\n\n"
                                 "可重试；若反复出现请把提示截图反馈。" % reason)

    def _finish(self, done_dirs, total_dirs, stopped=False):
        """全部跑完（stopped=False）或中途停止（stopped=True）后的收尾与总结。"""
        self.running = False
        self._stop_indet()
        self.btn_run.configure_state("normal", "开始识别")
        self.btn_stop.configure_state("disabled", "停止")
        self.btn_open.configure_state("normal")
        self.btn_export.configure_state("normal" if self.dir_results else "disabled")
        # 只有一个目录时「打开所在文件夹」进它的工作目录（旧版行为）；多目录时进根目录
        if len(self.dir_results) == 1:
            self.work_dir = self.dir_results[0]["work"]
        else:
            self.work_dir = self.root_dir or self.folder.get().strip()

        n = len(self.records)
        ok_n = sum(1 for r in self.records if r["ok"])
        fail_n = n - ok_n
        copied = sum(d["copied"] for d in self.dir_results)
        self.progress.set_value(done_dirs * 100.0 / total_dirs if total_dirs else 0.0,
                                stopped=stopped or not self.records)
        self._update_stats()

        ex_rows = [(d, d["excel"], os.path.join(d["work"], OUT_XLSX))
                   for d in self.dir_results if d.get("excel")]
        lines = []
        if stopped:
            self.lbl_stat.configure(text="已停止：完成 %d/%d 个文件夹" % (done_dirs, total_dirs))
            lines.append("已停止：处理到第 %d/%d 个文件夹，本次成功 %d 张、未识别 %d 张。"
                         % (done_dirs, total_dirs, ok_n, fail_n))
            lines.append("已识别的结果全部保留；下次点「开始识别」会接着处理剩余内容"
                         "（已转好的 PDF 页图直接复用，不会重复转换）。")
        else:
            self.lbl_stat.configure(text="完成：成功 %d / 未识别 %d" % (ok_n, fail_n))
            lines.append("识别完成：共 %d 个文件夹，成功 %d 张、未识别 %d 张"
                         "（已复制到各自的「%s」共 %d 张）。"
                         % (len(self.dir_results), ok_n, fail_n, UNKNOWN_DIR, copied))

        if ex_rows:
            lines.append("")
            lines.append("Excel 已生成（每个文件夹各一份）：")
            for d, _i, path in ex_rows[:MAX_LIST_DIRS]:
                lines.append("  · [%s] %s" % (dir_label(d["rel"]), path))
            if len(ex_rows) > MAX_LIST_DIRS:
                lines.append("  · …另有 %d 个文件夹的 Excel（可点「打开所在文件夹」查看）"
                             % (len(ex_rows) - MAX_LIST_DIRS))
            lines.append("合计：票据 %d 张 / 明细 %d 行 / 价税合计 ¥%s"
                         % (sum(i["tickets"] for _d, i, _p in ex_rows),
                            sum(i["details"] for _d, i, _p in ex_rows),
                            format(sum(i["total"] for _d, i, _p in ex_rows), ",.2f")))

        pre = [s for s in (prep_summary(d["prep"]) for d in self.dir_results) if s]
        if pre:
            lines.append("")
            lines.append("PDF 预处理：%s" % "；".join(pre[:MAX_LIST_DIRS]))

        empty = [d for d in self.dir_results if not d["recs"]]
        if empty:
            lines.append("")
            lines.append("没有可识别图片的文件夹：%s"
                         % "、".join(dir_label(d["rel"]) for d in empty[:MAX_LIST_DIRS]))
        bad = [(d["rel"], nm, r) for d in self.dir_results for nm, r in d["prep"]["failed"]]
        if bad:
            lines.append("")
            lines.append("PDF 转换失败 %d 个（已归入各自的「%s」）：" % (len(bad), UNKNOWN_DIR))
            for rel, nm, r in bad[:MAX_LIST_DIRS]:
                lines.append("  · [%s] %s（%s）" % (dir_label(rel), nm, r))

        if ex_rows:
            self.lbl_out.configure(text="已导出 %d 份 Excel" % len(ex_rows))
        if lines:
            messagebox.showinfo(APP_TITLE, "\n".join(lines))

    def export(self):
        """重新导出：把本次每个文件夹的 Excel 再写一遍（只含识别成功的行）。"""
        if not self.dir_results:
            return None
        bad = []
        for d in self.dir_results:
            if not d["recs"]:
                continue
            try:
                d["excel"] = export_dir(d["work"], d["recs"])
            except Exception as e:  # noqa: BLE001
                bad.append("%s（%s）" % (dir_label(d["rel"]), e))
        self.btn_export.configure_state("normal")
        n_ex = len([d for d in self.dir_results if d.get("excel")])
        self.lbl_out.configure(text="已重新导出 %d 份 Excel" % n_ex)
        if bad:
            messagebox.showerror(APP_TITLE, "以下文件夹导出失败：\n" + "\n".join(bad))
        return [d["excel"] for d in self.dir_results if d.get("excel")]


def _selftest(folder):
    """打包产物的无界面自检（InvoiceOcrTool.exe --selftest <文件夹>）。

    因为 --windowed 的 exe 没有控制台，结果同时写到目标目录的 selftest.log，
    退出码 0 表示转换成功、1 表示失败。用于发版后验证包内的 PDF 渲染库可用。
    """
    lines = []

    def say(m):
        lines.append(str(m))

    res = prepare(folder, enabled=True, log=say)
    work = res["work_dir"]
    imgs = [f for f in sorted(os.listdir(work))
            if os.path.splitext(f)[1].lower() in IMG_EXTS and not f.startswith("_")]
    lines.append("work_dir=%s" % work)
    lines.append("pdf_count=%d rendered=%d reused=%d copied_images=%d images=%d"
                 % (res["pdf_count"], res["rendered"], res["reused"],
                    res["copied_images"], len(imgs)))
    for n, r in res["failed"]:
        lines.append("FAILED %s -> %s" % (n, r))
    lines.append("RESULT=%s" % ("OK" if imgs else "NO_IMAGE"))
    text = "\n".join(lines)
    try:
        with open(os.path.join(folder, "selftest.log"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    try:
        if sys.stdout:
            print(text, flush=True)
    except Exception:
        pass
    return 0 if imgs else 1


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        raise SystemExit(_selftest(sys.argv[2]))
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
