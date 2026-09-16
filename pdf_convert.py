#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF 预处理：把文件夹里的 PDF 逐页转成 JPG，作为 OCR 之前的前置步骤。

对外只暴露一个函数 prepare(folder, enabled=True) -> dict，
返回的 work_dir 就是后续 OCR 应该处理的目录：

    目录里没有 PDF            -> 原样返回该文件夹，不做任何写入（纯图片场景零副作用）
    只有 PDF / PDF 与图片混装 -> 新建「处理后」文件夹，PDF 每页渲染成 JPG 存进去，
                                 原始图片也复制进去，返回「处理后」目录

命名与归置约定（建哥 2026-09-16 确认，勿随意改）：
    - 命名：票据.pdf -> 票据_1.jpg（原始文件名 + 下划线 + 页码，页码从 1 起）
    - 目录：处理后
    - 原始 PDF 保留在原文件夹，不删除、不移动
    - 幂等：同名产物已存在就跳过，重复运行不会产生 _2 / _3 副本
    - 转换失败的 PDF（加密 / 损坏）复制到「处理后/未识别/」，与原 OCR 未识别文件放在一起

PyMuPDF 是延迟导入的：纯图片场景根本不会加载它，启动速度不受影响。
"""
import os
import shutil

from parser import IMG_EXTS

PDF_EXTS = {".pdf"}
WORK_DIR = "处理后"
UNKNOWN_DIR = "未识别"
SKIP_PREFIX = "_"

# 渲染尺度：parser.py 里的列匹配容差(175px)、行聚类阈值(22px) 都是按手机拍摄 /
# 扫描件那类「宽度 1200~1900px、文字高约 29px」的图调出来的**固定像素值**。
# PDF 渲染出来的图一旦大出一截，列会配错、相邻行会被合并成一行，明细就全丢了
# （但发票号码、价税合计仍然认得出来，症状极有迷惑性）。
# 所以这里把输出宽度卡在 1700px 以内：图片型 PDF 的字高与页面宽度成正比，
# 宽度固定后字高自然也落回 25~28px 的甜点区；矢量型 PDF（A4 页面 10pt 字）
# 在 200 DPI 下字高约 28px，同样合适。
TARGET_WIDTH = 1700
MAX_DPI = 200


def _new_pymupdf():
    """延迟导入 PyMuPDF（优先新包名，兼容旧的 fitz 导入方式）。"""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz as pymupdf
        return pymupdf


def scan(folder):
    """扫描第一层文件（与主程序口径一致：跳过 _ 开头的文件、不递归子目录）。"""
    imgs, pdfs = [], []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return imgs, pdfs
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or name.startswith(SKIP_PREFIX):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in IMG_EXTS:
            imgs.append(name)
        elif ext in PDF_EXTS:
            pdfs.append(name)
    return imgs, pdfs


def zoom_for(page):
    """按页面宽度算渲染倍率：输出宽不超过 TARGET_WIDTH，最高 MAX_DPI。"""
    base = MAX_DPI / 72.0
    w = float(getattr(page.rect, "width", 0) or 0)
    if w <= 0:
        return base
    return min(base, TARGET_WIDTH / w)


def render_pdf(pymupdf, doc, pdf_name, out_dir):
    """把已打开的 PDF 逐页渲染成 JPG。返回 (新增页数, 已存在跳过页数)。"""
    stem = os.path.splitext(pdf_name)[0]
    rendered = reused = 0
    for i, page in enumerate(doc, 1):
        dst = os.path.join(out_dir, "%s_%d.jpg" % (stem, i))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            reused += 1
            continue
        z = zoom_for(page)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z))
        pix.save(dst)
        rendered += 1
    return rendered, reused


def prepare(folder, enabled=True, log=None):
    """前置准备。返回 dict：
        work_dir       后续 OCR 的工作目录
        new_dir        是否使用了「处理后」目录
        pdf_count      PDF 文件数
        rendered       本次新渲染的页数
        reused         已存在而跳过的页数
        copied_images  复制进工作目录的原始图片数
        skipped_images 因已存在而跳过的图片数
        failed         [(文件名, 原因), ...]，也即复制到 未识别/ 的 PDF
    """
    say = log or (lambda *a: None)
    res = {"work_dir": folder, "new_dir": False, "pdf_count": 0, "rendered": 0,
           "reused": 0, "copied_images": 0, "skipped_images": 0, "failed": []}

    imgs, pdfs = scan(folder)
    if not enabled or not pdfs:
        return res  # 纯图片（或用户关掉开关）：完全不动原目录

    work = os.path.join(folder, WORK_DIR)
    os.makedirs(work, exist_ok=True)
    res["work_dir"] = work
    res["new_dir"] = True
    res["pdf_count"] = len(pdfs)
    say("准备目录：%s" % WORK_DIR)

    # 1) 原始图片一并搬进工作目录
    for name in imgs:
        dst = os.path.join(work, name)
        if os.path.exists(dst):
            res["skipped_images"] += 1
            continue
        try:
            shutil.copy2(os.path.join(folder, name), dst)
            res["copied_images"] += 1
        except OSError as e:
            res["failed"].append((name, "复制失败：%s" % e))

    # 2) PDF 逐页转 JPG
    pymupdf = _new_pymupdf()
    for name in pdfs:
        say("转换 PDF：%s" % name)
        doc = None
        try:
            doc = pymupdf.open(os.path.join(folder, name))
            if doc.needs_pass:
                raise RuntimeError("PDF 已加密，需要密码")
            if doc.page_count <= 0:
                raise RuntimeError("PDF 没有可用的页面")
            rendered, reused = render_pdf(pymupdf, doc, name, work)
            res["rendered"] += rendered
            res["reused"] += reused
        except Exception as e:
            res["failed"].append((name, "%s" % e))
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    # 3) 转换失败的 PDF 归集到 未识别/，等人工处理
    bad_pdfs = [n for n, _ in res["failed"] if os.path.splitext(n)[1].lower() in PDF_EXTS]
    if bad_pdfs:
        udir = os.path.join(work, UNKNOWN_DIR)
        os.makedirs(udir, exist_ok=True)
        for name in bad_pdfs:
            dst = os.path.join(udir, name)
            if not os.path.exists(dst):
                try:
                    shutil.copy2(os.path.join(folder, name), dst)
                except OSError:
                    pass

    return res


def summary(res):
    """把结果压成一行中文说明，给界面和日志用。"""
    if not res["new_dir"]:
        return ""
    total = res["rendered"] + res["reused"]
    parts = ["PDF %d 个 → 图片 %d 页" % (res["pdf_count"], total)]
    if res["reused"]:
        parts.append("其中 %d 页沿用已有文件" % res["reused"])
    if res["copied_images"] or res["skipped_images"]:
        parts.append("原始图片 %d 张" % (res["copied_images"] + res["skipped_images"]))
    if res["failed"]:
        parts.append("失败 %d 个" % len(res["failed"]))
    return "，".join(parts)
