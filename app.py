#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票识别汇总工具 - 主程序（Tkinter GUI）

选择文件夹 -> [前置] 有 PDF 则逐页转 JPG 到「处理后」目录
-> OCR 识别全部图片 -> 识别失败的图复制到 未识别/ 子文件夹
-> 导出 发票识别汇总.xlsx（票据汇总 + 明细）

PDF 前置步骤见 pdf_convert.py：目录里没有 PDF 时完全跳过，行为与旧版一致。
"""
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import autoupdate
from excel_out import export_excel
from parser import IMG_EXTS, parse_image
from pdf_convert import WORK_DIR, prepare, scan as scan_files, summary as prep_summary

VERSION = "1.3.1"
APP_TITLE = f"发票识别汇总工具 v{VERSION}"
OUT_XLSX = "发票识别汇总.xlsx"
UNKNOWN_DIR = "未识别"


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1080x720")
        root.minsize(920, 600)
        self._build_style()
        self._build_ui()
        self.q = queue.Queue()
        self.records = []
        self.folder = tk.StringVar()
        self.work_dir = None
        self.running = False
        self._poll()
        # 开窗之后再动更新的事，不拖慢启动
        self.root.after(600, self._post_start)

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
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TButton", font=("微软雅黑", 10))
        style.configure("TLabel", font=("微软雅黑", 10))
        style.configure("Title.TLabel", font=("微软雅黑", 16, "bold"),
                        foreground="#1F4E79")
        style.configure("Hint.TLabel", font=("微软雅黑", 9), foreground="#666666")
        style.configure("Treeview", font=("微软雅黑", 9), rowheight=24)
        style.configure("Treeview.Heading", font=("微软雅黑", 9, "bold"))

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="发票识别汇总", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text="选择文件夹，自动识别其中全部票据图片（本地离线 OCR，不上传）；"
                            "识别失败的图片自动复制到「未识别」子文件夹。"
                            "文件夹里有 PDF 时会先逐页转成 JPG 再识别。",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 8))

        row1 = ttk.Frame(top)
        row1.pack(fill="x")
        ttk.Label(row1, text="票据文件夹：").pack(side="left")
        self.ent = ttk.Entry(row1)
        self.ent.pack(side="left", fill="x", expand=True, padx=6)
        self.ent.bind("<KeyRelease>", lambda e: self.folder.set(self.ent.get().strip()))
        ttk.Button(row1, text="浏览…", command=self.pick_folder).pack(side="left", padx=(0, 8))
        self.btn_run = ttk.Button(row1, text="开始识别", command=self.start)
        self.btn_run.pack(side="left")

        row_pdf = ttk.Frame(top)
        row_pdf.pack(fill="x", pady=(6, 0))
        self.var_pdf = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_pdf, variable=self.var_pdf,
                        text="自动把 PDF 转成图片（PDF 逐页转 JPG，连同原有图片一起转入「%s」文件夹后再识别）"
                             % WORK_DIR).pack(side="left")

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=6)
        self.progress = ttk.Progressbar(row2, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_stat = ttk.Label(row2, text="就绪", width=32, anchor="e")
        self.lbl_stat.pack(side="left", padx=(8, 0))

        cols = ("file", "no", "date", "buyer", "seller", "nitems", "total", "state")
        heads = ("文件名", "发票号码", "开票日期", "购买方", "销售方", "项目数",
                 "价税合计", "状态")
        widths = (260, 165, 85, 110, 180, 55, 90, 70)
        frame = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w" if c in ("file", "buyer", "seller")
                             else "center")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("fail", foreground="#C00000")

        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")
        self.btn_open = ttk.Button(bottom, text="打开所在文件夹",
                                   command=self.open_folder, state="disabled")
        self.btn_open.pack(side="left")
        ttk.Label(bottom, text="v" + VERSION, style="Hint.TLabel").pack(side="left", padx=(10, 0))
        self.lbl_out = ttk.Label(bottom, text="", style="Hint.TLabel")
        self.lbl_out.pack(side="left", padx=10)
        self.btn_export = ttk.Button(bottom, text="重新导出 Excel",
                                     command=self.export, state="disabled")
        self.btn_export.pack(side="right")
        ttk.Button(bottom, text="检查更新",
                   command=self.check_update).pack(side="right", padx=(0, 8))

    # ---------- actions ----------
    def pick_folder(self):
        d = filedialog.askdirectory(title="选择票据所在文件夹（图片 / PDF）")
        if d:
            self.folder.set(d)
            self.ent.delete(0, "end")
            self.ent.insert(0, d)

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
        self.btn_run.state(["disabled"])
        self.btn_export.state(["disabled"])
        self.btn_open.state(["disabled"])
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.lbl_out.configure(text="")
        self.progress.configure(mode="indeterminate", maximum=1, value=0)
        self.progress.start(12)
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
                elif kind == "stage":
                    self.lbl_stat.configure(text=msg[1][:34])
                elif kind == "prep":
                    prep, n = msg[1], msg[2]
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(n, 1), value=0)
                    if prep["new_dir"]:
                        self.lbl_stat.configure(
                            text="PDF 已转 %d 页，开始识别…" % (prep["rendered"] + prep["reused"]))
                    else:
                        self.lbl_stat.configure(text="0/%d" % n)
                elif kind == "progress":
                    self.progress.configure(value=msg[1])
                    self.lbl_stat.configure(text=f"{msg[1]}/{msg[2]}  {msg[3][:24]}")
                elif kind == "done":
                    self._finish(msg[1], msg[2], msg[3])
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _finish(self, work, prep, img_n):
        self.running = False
        try:
            self.progress.stop()
        except Exception:
            pass
        self.work_dir = work
        self.btn_run.state(["!disabled"])
        self.btn_open.state(["!disabled"])

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
        self.btn_export.state(["!disabled"])
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
