# -*- coding: utf-8 -*-
"""app.py 端到端冒烟：真 Tk 根窗 + 真线程跑完整 _worker（OCR 引擎与识别打桩）。

为什么打桩：RapidOCR 加载要几秒且依赖模型文件，冒烟要的是「目标解析 → 前置 →
逐张识别 → 归档 → 导出 → 收尾消息」这条链路的正确性，不是 OCR 精度本身。
`parse_image` 与 `rapidocr_onnxruntime` 都被替换成假实现，全链路其余部分是真跑。

跑法（需要带 tkinter + pymupdf + openpyxl 的解释器，本项目为构建 venv）：
    python tests/test_app_filemode.py

⚠️ 全流程只用**一个 Tk 根窗与一个 App 实例**（四组场景之间手工重置状态）。
同一个进程里反复 destroy 再新建 Tk，会在解释器收尾阶段触发
`Tcl_AsyncDelete: async handler deleted by the wrong thread` 直接杀掉进程，
所以最后用 os._exit 退出（跳过 Tk 的收尾清理）。

覆盖：
  ① 文件模式（直接添加 PDF）→ 同级同名文件夹 + Excel + 记录
  ② 文件模式不勾选 PDF 开关 → 明确提示、不误跑
  ③ 文件夹模式回归（原有入口行为不变）
  ④ 文件模式下「停止」同样生效
"""
import os
import shutil
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pymupdf  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

# ---- 打桩：必须在 import app 之前塞进 sys.modules（app 里是函数内延迟 import） ----
_stub = types.ModuleType("rapidocr_onnxruntime")


class _FakeEngine:
    def __call__(self, path):
        return ([], None)


_stub.RapidOCR = _FakeEngine
sys.modules["rapidocr_onnxruntime"] = _stub

import app as A  # noqa: E402

CASE = os.path.join(ROOT, ".pybuild_cache", "appsmoke_%s" % time.strftime("%Y%m%d_%H%M%S"))
PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
        print("  ok   %s = %r" % (label, got))
    else:
        FAIL.append(label)
        print("  FAIL %s: 实际 %r 期望 %r" % (label, got, want))


def stub_parse(eng, path):
    return {"file": os.path.basename(path), "invoice_no": "0017681151",
            "date": "2025-08-11", "buyer": "某某医院", "seller": "某某药房",
            "items": [{"name": "检查费", "qty": "1", "amount": 100.0}],
            "total": 106.0, "ok": True, "reason": ""}


def synth(path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)

        def put(x, y, t, size=11):
            try:
                pg.insert_text((x, y), t, fontsize=size, fontname="china-s")
            except Exception:  # noqa: BLE001
                pg.insert_text((x, y), "INVOICE SAMPLE", fontsize=size)

        put(180, 60, "电子发票（普通发票）", 16)
        put(60, 110, "发票号码：001768115%d" % i)
        put(60, 140, "开票日期：2025年08月11日")
        put(60, 190, "购买方名称：某某医院")
        put(60, 220, "销售方名称：某某药房")
        put(60, 290, "项目名称")
        put(450, 290, "金额")
        y = 320
        for r in range(3):
            put(60, y, "检查费")
            put(450, y, "%.2f" % (100.0 * (r + 1)))
            y += 30
        put(60, y + 40, "价税合计（小写）￥106.00")
    doc.save(path)
    doc.close()


def jpgs(d):
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".jpg"))


def quiet():
    for fn in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, fn, lambda *a, **k: None)


def reset(ap):
    """同一 App 实例上重置到「可再次 start()」的状态。"""
    ap.records = []
    ap.dir_results = []
    ap.work_dir = None
    ap.cur_dir = "."
    ap._stop.clear()
    for i in ap.tree.get_children():
        ap.tree.delete(i)
    ap._update_stats()


def pump(root, ap, timeout=90):
    t0 = time.time()
    while ap.running and time.time() - t0 < timeout:
        root.update()
        time.sleep(0.03)
    for _ in range(25):
        root.update()
        time.sleep(0.02)


def main():
    quiet()
    A.parse_image = stub_parse
    if os.path.isdir(CASE):
        shutil.rmtree(CASE, ignore_errors=True)
    os.makedirs(CASE)
    pdf = os.path.join(CASE, "单件票据.pdf")
    synth(pdf, pages=2)
    folder = os.path.join(CASE, "文件夹模式")
    os.makedirs(folder)
    synth(os.path.join(folder, "目录票据.pdf"), pages=2)
    print("样本已生成 -> %s" % CASE)

    root = tk.Tk()
    root.withdraw()
    ap = A.App(root)
    # 关掉启动 600ms 后的自动更新检查（会走网络，冒烟不需要）
    if ap._post_job:
        try:
            root.after_cancel(ap._post_job)
        except Exception:  # noqa: BLE001
            pass
        ap._post_job = None

    print("\n[1] 文件模式：直接添加 PDF → 同级同名文件夹 + Excel")
    out = os.path.join(CASE, "单件票据")
    ap.mode, ap.files = "files", [pdf]
    ap.var_pdf.set(True)
    ap.start()
    pump(root, ap)
    check("已结束", ap.running, False)
    check("同级同名文件夹已建", os.path.isdir(out), True)
    check("页图已生成", jpgs(out), ["单件票据_1.jpg", "单件票据_2.jpg"])
    check("记录了 2 条", len(ap.records), 2)
    check("目录结果 1 条", len(ap.dir_results), 1)
    check("work_dir 指向该文件夹", ap.dir_results[0]["work"], out)
    check("Excel 已导出", os.path.exists(os.path.join(out, "发票识别汇总.xlsx")), True)

    print("\n[2] 文件模式 + 未勾选 PDF 开关 → 明确提示、不误跑")
    reset(ap)
    pre = sorted(os.listdir(out))
    ap.mode, ap.files = "files", [pdf]
    ap.var_pdf.set(False)
    ap.start()
    pump(root, ap)
    check("已结束", ap.running, False)
    check("没有新增记录", len(ap.records), 0)
    check("目录内容未变", sorted(os.listdir(out)), pre)

    print("\n[3] 文件夹模式回归：原有入口行为不变")
    reset(ap)
    ap.mode, ap.files = "folder", []
    ap.folder.set(folder)
    ap.var_pdf.set(True)
    ap.start()
    pump(root, ap)
    check("已结束", ap.running, False)
    wd = os.path.join(folder, "处理后")
    check("仍走「处理后」目录", os.path.isdir(wd), True)
    check("页图命名不变", jpgs(wd), ["目录票据_1.jpg", "目录票据_2.jpg"])
    check("Excel 在「处理后」", os.path.exists(os.path.join(wd, "发票识别汇总.xlsx")), True)
    check("记录 2 条", len(ap.records), 2)

    print("\n[4] 文件模式下「停止」同样生效")
    reset(ap)
    multi = os.path.join(CASE, "多页件.pdf")
    synth(multi, pages=6)
    ap.mode, ap.files = "files", [multi]
    ap.var_pdf.set(True)
    ap.start()
    ap.stop()
    pump(root, ap)
    check("已结束且未卡死", ap.running, False)

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    # ⚠️ 用 os._exit 跳过 Tk 收尾：多线程 + Tk 变量析构会触发
    #    Tcl_AsyncDelete 把进程直接杀掉，并污染退出码。
    sys.stdout.flush()
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
