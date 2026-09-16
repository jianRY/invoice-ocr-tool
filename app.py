#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票识别汇总工具 - 主程序（Tkinter GUI）

选择文件夹 -> OCR 识别全部图片 -> 识别失败的图复制到 未识别/ 子文件夹
-> 导出 发票识别汇总.xlsx（票据汇总 + 明细）
"""
import os
import queue
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from excel_out import export_excel
from parser import IMG_EXTS, parse_image

VERSION = "1.1.1"
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
        self.running = False
        self._poll()

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
                            "识别失败的图片自动复制到「未识别」子文件夹。",
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
        self.lbl_out = ttk.Label(bottom, text="", style="Hint.TLabel")
        self.lbl_out.pack(side="left", padx=10)
        self.btn_export = ttk.Button(bottom, text="重新导出 Excel",
                                     command=self.export, state="disabled")
        self.btn_export.pack(side="right")

    # ---------- actions ----------
    def pick_folder(self):
        d = filedialog.askdirectory(title="选择票据图片所在文件夹")
        if d:
            self.folder.set(d)
            self.ent.delete(0, "end")
            self.ent.insert(0, d)

    def open_folder(self):
        d = self.folder.get()
        if d and os.path.isdir(d):
            os.startfile(d)

    def start(self):
        if self.running:
            return
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(APP_TITLE, "请先选择有效的文件夹")
            return
        imgs = [f for f in os.listdir(folder)
                if os.path.splitext(f)[1].lower() in IMG_EXTS and not f.startswith("_")]
        if not imgs:
            messagebox.showinfo(APP_TITLE, "该文件夹下没有找到图片文件")
            return
        self.running = True
        self.records = []
        self.btn_run.state(["disabled"])
        self.btn_export.state(["disabled"])
        self.btn_open.state(["disabled"])
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.progress.configure(maximum=len(imgs), value=0)
        self.lbl_stat.configure(text=f"0/{len(imgs)}")
        threading.Thread(target=self._worker, args=(folder, imgs),
                         daemon=True).start()

    def _worker(self, folder, imgs):
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()
        for i, name in enumerate(imgs, 1):
            rec = parse_image(eng, os.path.join(folder, name))
            self.q.put(("row", rec))
            self.q.put(("progress", i, len(imgs), name))
        self.q.put(("done", folder))

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
                elif kind == "progress":
                    self.progress.configure(value=msg[1])
                    self.lbl_stat.configure(text=f"{msg[1]}/{msg[2]}  {msg[3][:24]}")
                elif kind == "done":
                    self._finish(msg[1])
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _finish(self, folder):
        self.running = False
        self.btn_run.state(["!disabled"])
        self.btn_open.state(["!disabled"])
        unknown = [r for r in self.records if not r["ok"]]
        copied = 0
        if unknown:
            udir = os.path.join(folder, UNKNOWN_DIR)
            os.makedirs(udir, exist_ok=True)
            for r in unknown:
                src = os.path.join(folder, r["file"])
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
            msg = (f"识别完成：成功 {ok_n} 张，未识别 {len(unknown)} 张"
                   f"（已复制到「{UNKNOWN_DIR}」文件夹 {copied} 张）。\n\n"
                   f"Excel 已生成：{os.path.join(folder, OUT_XLSX)}\n"
                   f"票据 {info['tickets']} 张 / 明细 {info['details']} 行 / "
                   f"价税合计 ¥{info['total']:,.2f}")
            messagebox.showinfo(APP_TITLE, msg)
            self.lbl_out.configure(text=f"已导出 {OUT_XLSX}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"导出 Excel 失败：{e}")

    def export(self):
        folder = self.folder.get().strip()
        good = [r for r in self.records if r["ok"]]
        out = os.path.join(folder, OUT_XLSX)
        info = export_excel(good, out)
        self.btn_export.state(["!disabled"])
        return info


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
