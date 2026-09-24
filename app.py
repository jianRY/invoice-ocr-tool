#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票识别汇总工具 - 主程序（Tkinter GUI，v1.3.2 精致卡片界面）

选择文件夹 -> [前置] 有 PDF 则逐页转 JPG 到「处理后」目录
-> OCR 识别全部图片 -> 识别失败的图复制到 未识别/ 子文件夹
-> 导出 发票识别汇总.xlsx（票据汇总 + 明细）

界面（方案 B）：品牌头 + 圆角卡片 + 实时统计卡（票据/成功/未识别/价税合计），
自绘按钮 / 复选框 / 进度条；业务逻辑与 v1.3.1 完全一致。

PDF 前置步骤见 pdf_convert.py：目录里没有 PDF 时完全跳过，行为与旧版一致。

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
from pdf_convert import WORK_DIR, prepare, scan as scan_files, summary as prep_summary

VERSION = "1.3.2"
APP_TITLE = f"发票识别汇总工具 v{VERSION}"
OUT_XLSX = "发票识别汇总.xlsx"
UNKNOWN_DIR = "未识别"

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
        self.records = []
        self.folder = tk.StringVar()
        self.work_dir = None
        self.running = False
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

        # ── 品牌头（flat：浅底 logo + 标题 + 副标题） ──
        head = tk.Canvas(root, bg=sk.bg, highlightthickness=0, bd=0,
                         height=K.u(64))
        head.pack(fill="x")
        K.logo_mark(head, K.u(20), K.u(12), K.u(40), sk)
        head.create_text(K.u(74), K.u(12), text="发票识别汇总",
                         font=K.f(15, True), fill=sk.text, anchor="nw")
        head.create_text(K.u(76), K.u(40), anchor="w",
                         text="本地离线 OCR · 自动汇总 Excel · 数据不上传",
                         font=K.f(9), fill=sk.muted)

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

        row2 = tk.Frame(c_folder.body, bg=sk.card)
        row2.pack(fill="x", pady=(K.u(10), 0))
        self.var_pdf = tk.BooleanVar(value=True)
        chk = K.RoundCheck(row2, sk, variable=self.var_pdf,
                           text="自动把 PDF 转成图片（PDF 逐页转 JPG，连同原有图片"
                                "一起转入「%s」文件夹后再识别）" % WORK_DIR)
        chk.pack(fill="x", anchor="w")   # fill 拉伸至 body 宽，长文案不被裁

        # ── 统计卡行（4 张；⚠️ 必须显式给宽，否则互相抢宽被挤丢） ──
        srow = tk.Frame(root, bg=sk.bg)
        srow.pack(fill="x", padx=K.u(16), pady=(K.u(10), 0))
        self.stat_labels = {}
        for key, cap in STATS:
            c = K.Card(srow, sk, pad=12)
            c.configure(width=K.u(220))
            c.pack(side="left", expand=True, fill="x", padx=(0, K.u(10)))
            lab = tk.Label(c.body, text="—", font=K.f(15, True),
                           fg=sk.accent_d, bg=sk.card)
            lab.pack(anchor="w")
            tk.Label(c.body, text=cap, font=K.f(9), fg=sk.muted,
                     bg=sk.card).pack(anchor="w")
            self.stat_labels[key] = lab

        # ── 进度卡 ──
        c_prog = K.Card(root, sk, pad=12)
        c_prog.pack(fill="x", padx=K.u(16), pady=(K.u(10), 0))
        self.progress = K.RoundProgress(c_prog.body, sk, height=10)
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_stat = tk.Label(c_prog.body, text="就绪", font=K.f(9),
                                 fg=sk.muted, bg=sk.card, width=32, anchor="e")
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
        imgs, pdfs = scan_files(folder)
        if not imgs and not (use_pdf and pdfs):
            if pdfs:
                messagebox.showinfo(APP_TITLE, "该文件夹里只有 PDF，请勾选下面的「自动把 PDF 转成图片」")
            else:
                messagebox.showinfo(APP_TITLE, "该文件夹下没有找到图片或 PDF 文件")
            return
        self.running = True
        self.records = []
        self.work_dir = None
        self.btn_run.configure_state("disabled", "识别中…")
        self.btn_export.configure_state("disabled")
        self.btn_open.configure_state("disabled")
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._update_stats()
        self.lbl_out.configure(text="")
        self._start_indet()
        self.lbl_stat.configure(text="准备文件…")
        threading.Thread(target=self._worker, args=(folder, use_pdf),
                         daemon=True).start()

    def _worker(self, folder, use_pdf):
        prep = prepare(folder, enabled=use_pdf,
                       log=lambda m: self.q.put(("stage", m)))
        work = prep["work_dir"]
        imgs = [f for f in sorted(os.listdir(work))
                if os.path.splitext(f)[1].lower() in IMG_EXTS and not f.startswith("_")]
        self.q.put(("prep", prep, len(imgs)))
        if not imgs:
            self.q.put(("done", work, prep, 0))
            return
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()
        for i, name in enumerate(imgs, 1):
            rec = parse_image(eng, os.path.join(work, name))
            self.q.put(("row", rec))
            self.q.put(("progress", i, len(imgs), name))
        self.q.put(("done", work, prep, len(imgs)))

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "row":
                    rec = msg[1]
                    self.records.append(rec)
                    total = f"{rec['total']:,.2f}" if rec["total"] is not None else "—"
                    self.tree.insert("", "end", tags=("fail",) if not rec["ok"] else (),
                                     values=(rec["file"], rec["invoice_no"] or "—",
                                             rec["date"] or "—", rec["buyer"] or "—",
                                             rec["seller"] or "—",
                                             len(rec["items"]) or "—", total,
                                             "成功" if rec["ok"] else "未识别"))
                    self._update_stats()
                elif kind == "stage":
                    self.lbl_stat.configure(text=msg[1][:34])
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
                    i, n, name = msg[1], msg[2], msg[3]
                    self.progress.set_value(i * 100.0 / max(n, 1))
                    self.lbl_stat.configure(text=f"{i}/{n}  {name[:24]}")
                elif kind == "done":
                    self._finish(msg[1], msg[2], msg[3])
        except queue.Empty:
            pass
        self._poll_job = self.root.after(120, self._poll)

    def _finish(self, work, prep, img_n):
        self.running = False
        self._stop_indet()
        self.work_dir = work
        self.btn_run.configure_state("normal", "开始识别")
        self.btn_open.configure_state("normal")
        self.progress.set_value(100.0 if img_n else 0.0, stopped=not img_n)
        self._update_stats()

        if not img_n:
            lines = ["没有可识别的图片文件。"]
            if prep["failed"]:
                lines.append("")
                lines.append("以下文件未能转换（已归入「%s」）：" % UNKNOWN_DIR)
                lines += ["  · %s（%s）" % (n, r) for n, r in prep["failed"]]
            if prep["new_dir"]:
                lines.append("")
                lines.append("工作目录：%s" % work)
            messagebox.showwarning(APP_TITLE, "\n".join(lines))
            self.lbl_stat.configure(text="无可用图片")
            return

        unknown = [r for r in self.records if not r["ok"]]
        copied = 0
        if unknown:
            udir = os.path.join(work, UNKNOWN_DIR)
            os.makedirs(udir, exist_ok=True)
            for r in unknown:
                src = os.path.join(work, r["file"])
                dst = os.path.join(udir, r["file"])
                if os.path.exists(src) and not os.path.exists(dst):
                    try:
                        shutil.copy2(src, dst)
                        copied += 1
                    except OSError:
                        pass
        ok_n = len(self.records) - len(unknown)
        try:
            info = self.export()
            lines = [f"识别完成：成功 {ok_n} 张，未识别 {len(unknown)} 张"
                     f"（已复制到「{UNKNOWN_DIR}」文件夹 {copied} 张）。"]
            s = prep_summary(prep)
            if s:
                lines.append("")
                lines.append("PDF 预处理：%s" % s)
                lines.append("工作目录：%s" % work)
            if prep["failed"]:
                lines.append("转换失败：%s" % "；".join("%s（%s）" % (n, r) for n, r in prep["failed"]))
            lines.append("")
            lines.append(f"Excel 已生成：{os.path.join(work, OUT_XLSX)}")
            lines.append(f"票据 {info['tickets']} 张 / 明细 {info['details']} 行 / "
                         f"价税合计 ¥{info['total']:,.2f}")
            messagebox.showinfo(APP_TITLE, "\n".join(lines))
            self.lbl_out.configure(text=f"已导出 {OUT_XLSX}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"导出 Excel 失败：{e}")

    def export(self):
        folder = self.work_dir or self.folder.get().strip()
        good = [r for r in self.records if r["ok"]]
        out = os.path.join(folder, OUT_XLSX)
        info = export_excel(good, out)
        self.btn_export.configure_state("normal")
        return info


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
