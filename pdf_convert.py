#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PDF 预处理：把文件夹里的 PDF 逐页转成 JPG，作为 OCR 之前的前置步骤。

对外暴露的函数：
    collect_dirs(root)                递归收集「含图片或 PDF」的全部目录（含根目录本身）
    prepare(folder, enabled)          单个目录的 PDF -> JPG 前置准备
    file_targets(paths)               把「直接选择的文件」归一成处理目标（去重保序）
    prepare_file(pdf_path)            单个 PDF 文件 -> 同级同名文件夹的逐页图片
    prepare_target(target, enabled)   统一前置入口（按 target 的类型分发到上面两个）

返回的 work_dir 就是后续 OCR 应该处理的目录：

    目录里没有 PDF            -> 原样返回该文件夹，不做任何写入（纯图片场景零副作用）
    只有 PDF / PDF 与图片混装 -> 新建「处理后」文件夹，PDF 每页渲染成 JPG 存进去，
                                 原始图片也复制进去，返回「处理后」目录

「直接选择文件」入口（v1.5.0 新增，与上面文件夹流程互不影响）：
    · 选中的是 PDF   -> 在同级建一个「以 PDF 主文件名命名」的文件夹，
                        逐页渲染成 JPG 存进去（渲染口径与文件夹流程完全一致），
                        再把该文件夹当普通工作目录接入原有识别流程
    · 选中的是图片   -> 不复制、不搬动，直接把它**所在目录**当普通文件夹走一遍原流程

命名与归置约定（定稿约定，勿随意改）：
    - 命名：票据.pdf -> 票据_1.jpg（原始文件名 + 下划线 + 页码，页码从 1 起）
    - 目录：处理后
    - 原始 PDF 保留在原文件夹，不删除、不移动
    - 幂等：同名产物已存在就跳过，重复运行不会产生 _2 / _3 副本
    - 转换失败的 PDF（加密 / 损坏）复制到「处理后/未识别/」，与原 OCR 未识别文件放在一起

PyMuPDF 是延迟导入的：纯图片场景根本不会加载它，启动速度不受影响。
"""
import os
import re
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


def collect_dirs(root):
    """递归收集「需要处理的目录」：只要该目录第一层含图片或 PDF 就算一个。

    返回 [{"rel": 相对路径, "path": 绝对路径, "imgs": 图片数, "pdfs": PDF数}, ...]。
    根的 rel 为 "."；顺序为目录树深度优先、同级按名称排序（根目录在最前）。

    ⚠️ 必须跳过程序自己生成的产物目录，否则会把上一轮转出的页图、「未识别」里的
    副本再识别一遍，而且会在「处理后/处理后」里无限套娃：
      · 处理后 / 未识别（WORK_DIR / UNKNOWN_DIR，任意层级）
      · 以 _ 开头的目录（与文件名共用同一条「跳过」约定）
    """
    out = []
    for cur, dirs, _files in os.walk(root):
        # 原地裁剪 dirs，os.walk 就不会再往这些目录里钻
        dirs[:] = sorted(d for d in dirs
                         if d not in (WORK_DIR, UNKNOWN_DIR)
                         and not d.startswith(SKIP_PREFIX))
        imgs, pdfs = scan(cur)
        if imgs or pdfs:
            out.append({"rel": os.path.relpath(cur, root), "path": cur,
                        "imgs": len(imgs), "pdfs": len(pdfs)})
    return out


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


def render_pdf(pymupdf, doc, pdf_name, out_dir, cancel=None):
    """把已打开的 PDF 逐页渲染成 JPG。返回 (新增页数, 已存在跳过页数)。

    cancel 是个可调用对象（通常传 threading.Event.is_set）：返回 True 时立刻收工，
    已渲染的页保留在磁盘上（下次运行按幂等规则直接复用，不会白转）。
    一页大图渲染要好几秒，所以中断点必须精确到「每页之前」。
    """
    stem = os.path.splitext(pdf_name)[0]
    rendered = reused = 0
    for i, page in enumerate(doc, 1):
        if cancel and cancel():
            break
        dst = os.path.join(out_dir, "%s_%d.jpg" % (stem, i))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            reused += 1
            continue
        z = zoom_for(page)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z))
        pix.save(dst)
        rendered += 1
    return rendered, reused


def prepare(folder, enabled=True, log=None, cancel=None):
    """前置准备。返回 dict：
        work_dir       后续 OCR 的工作目录
        new_dir        是否使用了「处理后」目录
        pdf_count      PDF 文件数
        rendered       本次新渲染的页数
        reused         已存在而跳过的页数
        copied_images  复制进工作目录的原始图片数
        skipped_images 因已存在而跳过的图片数
        failed         [(文件名, 原因), ...]，也即复制到 未识别/ 的 PDF
        warn           [提醒文本, ...]，如原图与 PDF 页图同名（不改产物，只提示）

    cancel 为可调用对象（threading.Event.is_set）：返回 True 时中止剩余 PDF 的转换，
    已转出的页保留在磁盘上，下次运行直接复用。
    """
    say = log or (lambda *a: None)
    res = {"work_dir": folder, "new_dir": False, "pdf_count": 0, "rendered": 0,
           "reused": 0, "copied_images": 0, "skipped_images": 0, "failed": [],
           "warn": []}

    imgs, pdfs = scan(folder)
    if not enabled or not pdfs:
        return res  # 纯图片（或用户关掉开关）：完全不动原目录

    work = os.path.join(folder, WORK_DIR)
    os.makedirs(work, exist_ok=True)
    res["work_dir"] = work
    res["new_dir"] = True
    res["pdf_count"] = len(pdfs)
    say("准备目录：%s" % WORK_DIR)

    # 1) PDF 逐页转 JPG
    # ⚠️ 顺序有讲究：**必须先渲染 PDF、再复制原始图片**。
    #    两者命名会撞车——原图恰好叫「某票据_1.jpg」，同目录又有「某票据.pdf」时：
    #      · 先复制原图 → render_pdf 按幂等规则把已有同名文件当成「已转好」跳过，
    #        真正的页图永远出不来，却拿那张原图去识别（静默用错图）
    #      · 先渲染 PDF → 页图先落盘，原图同名时计入 skipped_images，结果正确
    #    纯图片场景不受影响（没有 PDF 时下面这段根本不进），故对既有行为零改变。
    pymupdf = _new_pymupdf()
    for name in pdfs:
        if cancel and cancel():
            say("已请求停止，剩余 PDF 未转换")
            break
        say("转换 PDF：%s" % name)
        doc = None
        try:
            doc = pymupdf.open(os.path.join(folder, name))
            if doc.needs_pass:
                raise RuntimeError("PDF 已加密，需要密码")
            if doc.page_count <= 0:
                raise RuntimeError("PDF 没有可用的页面")
            rendered, reused = render_pdf(pymupdf, doc, name, work, cancel=cancel)
            res["rendered"] += rendered
            res["reused"] += reused
        except Exception as e:  # noqa: BLE001
            res["failed"].append((name, "%s" % e))
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001
                    pass

    # 2) 原始图片一并搬进工作目录
    for name in imgs:
        if cancel and cancel():
            say("已请求停止，剩余图片未复制")
            break
        dst = os.path.join(work, name)
        if os.path.exists(dst):
            res["skipped_images"] += 1
            _note_collision(res, name, pdfs)
            continue
        try:
            shutil.copy2(os.path.join(folder, name), dst)
            res["copied_images"] += 1
        except OSError as e:
            res["failed"].append((name, "复制失败：%s" % e))

    for m in res["warn"]:
        say(m)

    # 3) 转换失败的 PDF 归集到 未识别/，等人工处理（与 prepare_file 同一段逻辑）
    bad_pdfs = [n for n, _ in res["failed"] if os.path.splitext(n)[1].lower() in PDF_EXTS]
    for name in bad_pdfs:
        _park_failed_pdf(os.path.join(folder, name), work, name)

    return res


def _note_collision(res, img_name, pdfs):
    """原图与 PDF 转出的页图同名（罕见）：记一条提醒，不改变任何产物。

    只做检测与告知——命名规则是定稿约定，不能因为这点边角情况去改产物名。
    """
    stem = os.path.splitext(img_name)[0]
    for pdf in pdfs:
        pstem = os.path.splitext(pdf)[0]
        if re.fullmatch(re.escape(pstem) + r"_\d+", stem, re.IGNORECASE):
            msg = ("原图「%s」与「%s」转出的页图同名，已以 PDF 页图为准；"
                   "如需保留该原图请先改名再重跑" % (img_name, pdf))
            if msg not in res["warn"]:
                res["warn"].append(msg)
            return


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


# ---------------------------------------------------------------- 直接添加文件
# 与上面「文件夹流程」完全解耦：不改 prepare / collect_dirs / scan 的任何行为，
# 只按同一套渲染口径（zoom_for + render_pdf）把单个 PDF 摊进同级同名文件夹。
def file_targets(paths):
    """把用户「直接选择的文件」归一成处理目标列表（去重保序）。

    每个目标与 collect_dirs() 的条目同构，额外带 kind 与 src：
        {"kind": "pdf", "src": PDF绝对路径, "rel": 目标文件夹名, "path": 目标文件夹,
         "imgs": 0, "pdfs": 1}
        {"kind": "dir", "rel": 目录名, "path": 该图片所在目录, "imgs": n, "pdfs": m}

    · 选中的是 PDF：目标目录 = 同级 / 以主文件名命名（不含扩展名），此时尚未创建
    · 选中的是图片：按「整目录处理」语义，取它所在目录（**只处理该目录第一层**，
      不递归下钻，避免选一张图却把整棵子树都扫了）
    · 同一文件重复选择、同目录多张图片重复登记，一律去重
    · 不存在的路径直接跳过（界面层已过滤，这里再兜一层）
    """
    out, seen_files, seen_dirs = [], set(), set()
    for p in (paths or []):
        try:
            ap = os.path.abspath(p)
        except Exception:  # noqa: BLE001
            continue
        if ap in seen_files or not os.path.isfile(ap):
            continue
        ext = os.path.splitext(ap)[1].lower()
        if ext in PDF_EXTS:
            seen_files.add(ap)
            stem = os.path.splitext(os.path.basename(ap))[0]
            out.append({"kind": "pdf", "src": ap, "rel": stem,
                        "path": os.path.join(os.path.dirname(ap), stem),
                        "imgs": 0, "pdfs": 1})
        elif ext in IMG_EXTS:
            seen_files.add(ap)
            d = os.path.dirname(ap)
            if d in seen_dirs:           # 同目录只登记一次（整目录处理）
                continue
            seen_dirs.add(d)
            imgs, pdfs = scan(d)
            out.append({"kind": "dir", "rel": os.path.basename(d) or d,
                        "path": d, "imgs": len(imgs), "pdfs": len(pdfs)})
    return out


def prepare_file(pdf_path, log=None, cancel=None):
    """单个 PDF 文件的前置准备：同级建同名文件夹，逐页渲染成 JPG。

    返回结构与 prepare() 完全一致（多一个 created / reason 字段），
    这样上层的汇总、失败清单、未识别归档逻辑可以原样复用：

        work_dir       目标文件夹（同级、以 PDF 主文件名命名）
        new_dir        是否使用了独立文件夹（有 PDF 时恒为 True）
        created        本次是否新建了文件夹（False = 复用已存在的）
        pdf_count/rendered/reused/copied_images/skipped_images/failed
        reason         无法处理时给界面看的明确原因（成功时为空串）

    异常处理（每条都有明确原因，不全靠静默失败）：
        目标名被同名**文件**占用   -> 不创建、上报原因
        目标文件夹不存在           -> 新建（created=True）
        目标文件夹已存在           -> 直接复用，已存在的页按幂等规则跳过
        加密 / 损坏 / 无页 PDF     -> 上报原因，并把原件复制到 目标/未识别/
    """
    say = log or (lambda *a: None)
    name = os.path.basename(pdf_path)
    folder = os.path.dirname(os.path.abspath(pdf_path))
    stem = os.path.splitext(name)[0]
    res = {"work_dir": folder, "new_dir": False, "created": False, "pdf_count": 0,
           "rendered": 0, "reused": 0, "copied_images": 0, "skipped_images": 0,
           "failed": [], "warn": [], "reason": ""}

    if not os.path.isfile(pdf_path):
        res["reason"] = "文件不存在或不可读"
        res["failed"].append((name, res["reason"]))
        return res

    out = os.path.join(folder, stem)
    if os.path.exists(out) and not os.path.isdir(out):
        res["reason"] = "同级已有同名文件「%s」，无法创建文件夹（请先重命名其中一个）" % stem
        res["failed"].append((name, res["reason"]))
        return res
    if os.path.isdir(out):
        say("目标文件夹已存在，复用：%s" % stem)
    else:
        try:
            os.makedirs(out)
            res["created"] = True
        except OSError as e:
            res["reason"] = "创建文件夹「%s」失败：%s" % (stem, e)
            res["failed"].append((name, res["reason"]))
            return res

    res["work_dir"] = out
    res["new_dir"] = True
    res["pdf_count"] = 1
    say("转换 PDF：%s → %s\\" % (name, stem))

    pymupdf = _new_pymupdf()
    doc = None
    try:
        doc = pymupdf.open(pdf_path)
        if doc.needs_pass:
            raise RuntimeError("PDF 已加密，需要密码")
        if doc.page_count <= 0:
            raise RuntimeError("PDF 没有可用的页面")
        rendered, reused = render_pdf(pymupdf, doc, name, out, cancel=cancel)
        res["rendered"] = rendered
        res["reused"] = reused
    except Exception as e:  # noqa: BLE001
        res["reason"] = "%s" % e
        res["failed"].append((name, "%s" % e))
        _park_failed_pdf(pdf_path, out, name)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass
    return res


def _park_failed_pdf(pdf_path, out_dir, name):
    """转换失败的 PDF 复制一份到 目标/未识别/，等人工处理（原件原地不动）。"""
    try:
        udir = os.path.join(out_dir, UNKNOWN_DIR)
        os.makedirs(udir, exist_ok=True)
        dst = os.path.join(udir, name)
        if not os.path.exists(dst):
            shutil.copy2(pdf_path, dst)
    except OSError:
        pass


def prepare_target(target, enabled=True, log=None, cancel=None):
    """统一前置入口：按 target 的类型分发，调用方不必关心来源。

    · PDF 文件目标 -> prepare_file()（渲染进同级同名文件夹）
    · 其余（文件夹流程 / 图片导入的所在目录）-> prepare()
    """
    if (target or {}).get("kind") == "pdf":
        return prepare_file(target["src"], log=log, cancel=cancel)
    return prepare(target["path"], enabled=enabled, log=log, cancel=cancel)

