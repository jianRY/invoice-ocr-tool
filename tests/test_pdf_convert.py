# -*- coding: utf-8 -*-
"""pdf_convert.py 的场景测试（造样本 + 断言），不依赖任何外部图片。

跑法（需要带 pymupdf 的解释器，本项目为构建 venv）：
    "C:/Users/toxuj/.workbuddy/binaries/python/envs/court_build_v13/Scripts/python.exe" tests/test_pdf_convert.py

附加 OCR 端到端（有真实票据图片时）：
    ... tests/test_pdf_convert.py --images "D:\\票据样本"

合成样本用 PyMuPDF 现场画，任何机器都能跑；断言覆盖
「纯图片零副作用 / 纯 PDF / 图文混装 / 加密 PDF / 损坏 PDF / 12 页分页命名 /
幂等 / 输出宽度约束 / A4 扫描件」九组场景。
 """
import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pymupdf  # noqa: E402
from pdf_convert import (  # noqa: E402
    TARGET_WIDTH, UNKNOWN_DIR, WORK_DIR, prepare, zoom_for,
)

CASE_ROOT = os.path.join(ROOT, ".pybuild_cache", "pdftest_%s" % time.strftime("%Y%m%d_%H%M%S"))
PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
        print("  ok   %s = %r" % (label, got))
    else:
        FAIL.append(label)
        print("  FAIL %s: 实际 %r 期望 %r" % (label, got, want))


# ---------- 合成样本 ----------
def synth_page(doc, no, date, rows=3, w=595, h=842):
    """往 doc 里加一页「像发票的」矢量页面。"""
    pg = doc.new_page(width=w, height=h)

    def put(x, y, text, size=11):
        try:
            pg.insert_text((x, y), text, fontsize=size, fontname="china-s")
        except Exception:
            pg.insert_text((x, y), "INVOICE SAMPLE", fontsize=size)

    put(180, 60, "电子发票（普通发票）", 16)
    put(60, 110, "发票号码：%s" % no)
    put(60, 140, "开票日期：%s" % date)
    put(60, 190, "购买方名称：某某医院")
    put(60, 220, "销售方名称：某某药房")
    put(60, 290, "项目名称")
    put(330, 290, "数量")
    put(450, 290, "金额")
    y = 320
    for i in range(rows):
        put(60, y, "检查费" if i % 2 == 0 else "治疗费")
        put(330, y, "1")
        put(450, y, "%.2f" % (100.0 * (i + 1)))
        y += 30
    put(60, y + 40, "价税合计（小写）￥106.00")
    return pg


def synth_png(path, rows=3, w=900, h=1300):
    """合成一张位图票据（模拟拍照/扫描件）。"""
    doc = pymupdf.open()
    synth_page(doc, "0017681151", "2025年08月11日", rows, w, h)
    doc[0].get_pixmap(dpi=150).save(path)
    doc.close()


def img_pdf(paths, out, page=None):
    """page=None：页面尺寸=图片尺寸（扫描件打包）；page=(w,h)：图片按比例铺满该页面。"""
    doc = pymupdf.open()
    for p in paths:
        raw = pymupdf.open(p)
        pdfb = raw.convert_to_pdf()
        raw.close()
        src = pymupdf.open("pdf", pdfb)
        if page is None:
            doc.insert_pdf(src)
        else:
            pw, ph = page
            pg = doc.new_page(width=pw, height=ph)
            r = src[0].rect
            k = min(pw / r.width, ph / r.height)
            w, h = r.width * k, r.height * k
            pg.show_pdf_page(pymupdf.Rect((pw - w) / 2, (ph - h) / 2,
                                          (pw + w) / 2, (ph + h) / 2), src, 0)
        src.close()
    doc.save(out)
    doc.close()


def jpg_size(path):
    """真实像素尺寸（打开 JPG 得到的 rect 是 pt，会受 JPEG 的 DPI 标记影响）。"""
    px = pymupdf.Pixmap(path)
    return px.width, px.height


def files(d, sub=None):
    p = os.path.join(d, sub) if sub else d
    return sorted(os.listdir(p)) if os.path.isdir(p) else []


def build():
    if os.path.isdir(CASE_ROOT):
        shutil.rmtree(CASE_ROOT, ignore_errors=True)
    os.makedirs(CASE_ROOT)
    png = os.path.join(CASE_ROOT, "合成票据.png")
    synth_png(png)
    png2 = os.path.join(CASE_ROOT, "合成票据2.png")
    synth_png(png2, rows=2)

    d = os.path.join(CASE_ROOT, "case_allimg")
    os.makedirs(d)
    for i in (1, 2, 3):
        shutil.copy2(png, os.path.join(d, "票据%d.png" % i))

    d = os.path.join(CASE_ROOT, "case_purepdf")
    os.makedirs(d)
    for name, pages in (("单页发票.pdf", 1), ("三页票据.pdf", 3)):
        doc = pymupdf.open()
        for i in range(pages):
            synth_page(doc, "001768115%d" % i, "2025年08月11日")
        doc.save(os.path.join(d, name))
        doc.close()

    d = os.path.join(CASE_ROOT, "case_mixed")
    os.makedirs(d)
    doc = pymupdf.open()
    for i in range(3):
        synth_page(doc, "001768119%d" % i, "2025年08月11日")
    doc.save(os.path.join(d, "混合_票据.pdf"))
    doc.close()
    for i in (1, 2):
        shutil.copy2(png, os.path.join(d, "原始图片%d.png" % i))
    enc = pymupdf.open()
    enc.new_page()
    enc.save(os.path.join(d, "加密票据.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="o", user_pw="123")
    enc.close()

    d = os.path.join(CASE_ROOT, "case_broken")
    os.makedirs(d)
    with open(os.path.join(d, "损坏文件.pdf"), "wb") as f:
        f.write(b"%PDF-1.7 this is not a real pdf body")
    shutil.copy2(png, d)

    d = os.path.join(CASE_ROOT, "case_many")
    os.makedirs(d)
    doc = pymupdf.open()
    for i in range(12):
        synth_page(doc, "0017682%03d" % i, "2025年08月11日")
    doc.save(os.path.join(d, "多页票据.pdf"))
    doc.close()

    d = os.path.join(CASE_ROOT, "case_a4")
    os.makedirs(d)
    img_pdf([png, png2], os.path.join(d, "扫描件A4.pdf"), page=(595, 842))
    print("样本已生成 -> %s" % CASE_ROOT)


# ---------- 断言 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=None, help="真实票据图片目录，附带跑 OCR 端到端")
    args = ap.parse_args()

    build()

    print("\n[1] 纯图片目录：应完全跳过，零副作用")
    d = os.path.join(CASE_ROOT, "case_allimg")
    before = files(d)
    r = prepare(d)
    check("work_dir 就是原目录", r["work_dir"] == d, True)
    check("new_dir=False", r["new_dir"], False)
    check("目录内容未变", files(d), before)

    print("\n[2] 关掉开关：即使有 PDF 也不动")
    d = os.path.join(CASE_ROOT, "case_purepdf")
    r = prepare(d, enabled=False)
    check("work_dir 是原目录", r["work_dir"] == d, True)
    check("未新建处理后", WORK_DIR in files(d), False)

    print("\n[3] 纯 PDF：新建处理后，逐页转换")
    r = prepare(d)
    work = os.path.join(d, WORK_DIR)
    check("new_dir=True", r["new_dir"], True)
    check("pdf_count=2", r["pdf_count"], 2)
    check("渲染 4 页", r["rendered"], 4)
    check("无失败", r["failed"], [])
    want = sorted(["单页发票_1.jpg", "三页票据_1.jpg", "三页票据_2.jpg", "三页票据_3.jpg"])
    check("目录内容", files(work), want)

    print("\n[4] 混合目录：转 PDF + 复制原图 + 加密 PDF 归集")
    d = os.path.join(CASE_ROOT, "case_mixed")
    r = prepare(d)
    work = os.path.join(d, WORK_DIR)
    check("rendered=3", r["rendered"], 3)
    check("copied_images=2", r["copied_images"], 2)
    check("failed 有 1 个", len(r["failed"]), 1)
    check("失败的是加密 PDF", r["failed"][0][0], "加密票据.pdf")
    check("未识别目录含加密 PDF", files(work, UNKNOWN_DIR), ["加密票据.pdf"])
    check("处理后有 6 个文件", len(files(work)), 6)

    print("\n[5] 幂等：同一目录再跑一次，不产生任何副本")
    r2 = prepare(d)
    check("rendered=0", r2["rendered"], 0)
    check("reused=3", r2["reused"], 3)
    check("copied_images=0", r2["copied_images"], 0)
    check("skipped_images=2", r2["skipped_images"], 2)
    check("文件数没变", len(files(work)), 6)
    check("没有重复副本", [f for f in files(work) if "(2)" in f], [])

    print("\n[6] 损坏 PDF：失败归集，图片仍照常处理")
    d = os.path.join(CASE_ROOT, "case_broken")
    r = prepare(d)
    work = os.path.join(d, WORK_DIR)
    check("copied_images=1", r["copied_images"], 1)
    check("failed 有 1 个", len(r["failed"]), 1)
    check("未识别目录含损坏 PDF", files(work, UNKNOWN_DIR), ["损坏文件.pdf"])

    print("\n[7] 12 页 PDF：页码命名 1..12")
    d = os.path.join(CASE_ROOT, "case_many")
    r = prepare(d)
    work = os.path.join(d, WORK_DIR)
    check("rendered=12", r["rendered"], 12)
    names = [f for f in files(work) if f.endswith(".jpg")]
    check("首页名", "多页票据_1.jpg" in names, True)
    check("末页名", "多页票据_12.jpg" in names, True)
    check("页数", len(names), 12)

    print("\n[8] 输出尺寸：PDF 转出的页图宽度都不超过 %d px" % TARGET_WIDTH)
    over = []
    for case in ("case_purepdf", "case_mixed", "case_many", "case_a4"):
        wd = os.path.join(CASE_ROOT, case, WORK_DIR)
        src_names = set(os.listdir(os.path.join(CASE_ROOT, case)))
        for f in files(wd):
            if f.lower().endswith((".jpg", ".jpeg", ".png")) and f not in src_names:
                w, h = jpg_size(os.path.join(wd, f))
                if w > TARGET_WIDTH:
                    over.append((case, f, w))
    check("超宽图片", over, [])

    print("\n[9] A4 扫描件 PDF：正常转换且尺寸受控")
    d = os.path.join(CASE_ROOT, "case_a4")
    r = prepare(d)
    work = os.path.join(d, WORK_DIR)
    check("rendered=2", r["rendered"], 2)
    check("无失败", r["failed"], [])
    for f in files(work):
        if f.lower().endswith(".jpg"):
            w, h = jpg_size(os.path.join(work, f))
            print("     %s -> %dx%d" % (f, w, h))
            check("%s 宽度合规" % f, w <= TARGET_WIDTH, True)

    print("\n[10] 缩放倍率上限：不超过 200 DPI")

    class FakePage:
        def __init__(self, w):
            self.rect = type("R", (), {"width": w})()

    check("A4 页面(595pt)", round(zoom_for(FakePage(595)), 3), round(200 / 72.0, 3))
    check("超大页面(1909pt) 被限宽", round(zoom_for(FakePage(1909)) * 1909, 1),
          round(float(TARGET_WIDTH), 1))

    if args.images and os.path.isdir(args.images):
        print("\n[11] OCR 端到端（真实样本：%s）" % args.images)
        run_ocr(args.images)

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    print("样本目录：%s" % CASE_ROOT)
    return 1 if FAIL else 0


def run_ocr(src_dir):
    """把真实图片合成 PDF 后走完整链路：转换 -> OCR -> 导出 Excel。"""
    from excel_out import export_excel
    from parser import parse_image
    from rapidocr_onnxruntime import RapidOCR

    imgs = sorted(f for f in os.listdir(src_dir)
                  if f.lower().endswith((".jpg", ".jpeg", ".png")))[:6]
    if not imgs:
        print("     样本目录里没有图片，跳过")
        return
    d = os.path.join(CASE_ROOT, "case_ocr")
    os.makedirs(d, exist_ok=True)
    for n in imgs[:4]:
        shutil.copy2(os.path.join(src_dir, n), d)
    img_pdf([os.path.join(src_dir, n) for n in imgs[:3]],
            os.path.join(d, "样本票据.pdf"))

    prep = prepare(d, log=lambda m: print("     ", m))
    work = prep["work_dir"]
    todo = sorted(f for f in os.listdir(work)
                  if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png")
                  and not f.startswith("_"))
    print("     工作目录 %s，待识别 %d 张" % (work, len(todo)))
    eng = RapidOCR()
    recs = [parse_image(eng, os.path.join(work, n)) for n in todo]
    for r in recs:
        print("       %-24s %-4s %s %s %s" % (
            r["file"], "成功" if r["ok"] else "未识别", r["invoice_no"] or "-",
            r["date"] or "-", ("%.2f" % r["total"]) if r["total"] is not None else "-"))
    good = [r for r in recs if r["ok"]]
    info = export_excel(good, os.path.join(work, "发票识别汇总.xlsx"))
    check("PDF 转出的图能被识别", len(good) > 0, True)
    print("     票据 %d / 明细 %d / 价税合计 %.2f"
          % (info["tickets"], info["details"], info["total"]))


if __name__ == "__main__":
    raise SystemExit(main())
