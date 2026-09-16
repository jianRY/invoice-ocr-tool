#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发票识别汇总工具 —— 站点自动更新脚本（宝塔面板 / 任意 Linux 服务器）

作用：从 GitHub 拉取最新的静态展示页与版本信息，写入你自己的网站目录，
      可选把「单文件版 / 安装版」两个 exe 镜像到本站，供国内用户高速下载。

特点：
  * 只用 Python 3 标准库，不装任何第三方包（宝塔自带 python3 即可跑）
  * 多源回退：GitHub Pages -> raw.githubusercontent -> jsDelivr CDN -> 自定义源
  * 原子写入 + 自动备份，拉到的内容不合法时拒绝覆盖（站点不会被搞坏）
  * 幂等：内容没变就跳过，重复跑无副作用
  * 可选 --mirror-exe：把双 exe 下到本站 downloads/，并把页面里的下载链接
    改写为相对路径，国内用户直接从你的服务器下载，不再翻 GitHub

用法（宝塔「计划任务」里填一条命令即可）：
  python3 /www/wwwroot/invoice-ocr-tool/update_site.py
  python3 /www/wwwroot/invoice-ocr-tool/update_site.py --mirror-exe
  python3 /www/wwwroot/invoice-ocr-tool/update_site.py --dest /www/wwwroot/site --mirror-exe

默认配置见下方「默认配置」区，改常量或命令行传参均可。
退出码：0 = 成功（含无更新）；非 0 = 失败，便于计划任务报警。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

# ============================ 默认配置 ============================
# 站点目录：默认 = 本脚本所在目录（把脚本放进网站根目录即可，无需传参）
DEFAULT_DEST = os.path.dirname(os.path.abspath(__file__))

OWNER_REPO = "jianRY/invoice-ocr-tool"
BRANCH = "main"

# 展示页来源（按顺序尝试，第一个成功的就用）
PAGE_SOURCES = [
    "https://jianry.github.io/invoice-ocr-tool/index.html",
    "https://raw.githubusercontent.com/%s/%s/docs/index.html" % (OWNER_REPO, BRANCH),
    "https://cdn.jsdelivr.net/gh/%s@%s/docs/index.html" % (OWNER_REPO, BRANCH),
]

# exe 下载加速前缀（按顺序尝试；空串 = 直连 GitHub）
EXE_PREFIXES = [
    "",
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
]

BACKUP_DIR_NAME = ".site_backup"
BACKUP_KEEP = 5                 # 保留最近几份 index.html 备份
DEFAULT_KEEP_EXE_VERSIONS = 2   # downloads/ 里保留最近几个版本的 exe
MIRROR_SUBDIR = "downloads"

# 资产名形如 InvoiceOcrTool_v1.2.3.exe / InvoiceOcrTool_v1.2.3_setup.exe
EXE_NAME_RE = re.compile(r"InvoiceOcrTool_v(\d+(?:\.\d+)+)(_setup)?\.exe$")
EXE_VER_RE = re.compile(r"_v(\d+(?:\.\d+)+)")
# ==================================================================

UA = "Mozilla/5.0 (compatible; InvoiceOcrTool-SiteUpdater/1.0)"

# 页面合法性判据：必须同时含这些标记，否则视为错误页（如 404 / 拦截页）
PAGE_MARKERS = ['id="appVer"', "发票识别汇总工具", "InvoiceOcrTool_v"]


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def opener(proxy=None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [("User-Agent", UA)]
    return op


def http_get(url, timeout=45, proxy=None):
    op = opener(proxy)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with op.open(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url, timeout=45, proxy=None, token=None):
    op = opener(proxy)
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=h)
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------- 展示页 ----------------------------
def get_release(proxy=None, token=None):
    """查最新 Release，返回 (version, {资产名: {url, size}})。"""
    d = http_get_json("https://api.github.com/repos/%s/releases/latest" % OWNER_REPO,
                      proxy=proxy, token=token)
    ver = (d.get("tag_name") or "").lstrip("vV")
    assets = {}
    for a in d.get("assets", []):
        assets[a["name"]] = {"url": a["browser_download_url"], "size": a["size"]}
    return ver, assets, d.get("published_at", "")


def fetch_page(proxy=None, extra_sources=(), verbose=True):
    """按来源顺序拉展示页，返回 (bytes, 来源url)。内容不合法的源会被跳过。"""
    errs = []
    for url in list(extra_sources) + [u for u in PAGE_SOURCES if u not in extra_sources]:
        try:
            data = http_get(url, proxy=proxy)
            ok, why = validate_page(data)
            if not ok:
                errs.append("%s -> 内容不合法(%s)" % (url, why))
                if verbose:
                    log("  跳过 %s：内容不合法（%s）" % (url, why))
                continue
            return data, url
        except Exception as e:
            errs.append("%s -> %s" % (url, e))
            if verbose:
                log("  跳过 %s：%s" % (url, e))
    raise RuntimeError("所有页面来源均失败：\n  " + "\n  ".join(errs))


def validate_page(data):
    if not data or len(data) < 3000:
        return False, "内容过短(%d 字节)" % len(data or b"")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "非 UTF-8 文本"
    for mk in PAGE_MARKERS:
        if mk not in text:
            return False, "缺少标记 %s" % mk
    if "<html" not in text.lower():
        return False, "不是 HTML"
    return True, ""


def page_version(text):
    m = re.search(r'id="appVer"[^>]*>\s*v?([\d.]+)', text)
    if m:
        return m.group(1)
    m = re.search(r'InvoiceOcrTool_v([\d.]+)', text)
    return m.group(1) if m else ""


def write_atomic(path, data, keep_backup=True, backup_tag=""):
    """原子写入：先写临时文件再替换；替换前备份旧文件。"""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if keep_backup and os.path.exists(path):
        bdir = os.path.join(d, BACKUP_DIR_NAME)
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = ("_" + backup_tag) if backup_tag else ""
        shutil.copy2(path, os.path.join(bdir, "index_%s%s.html" % (stamp, tag)))
        olds = sorted(f for f in os.listdir(bdir) if f.startswith("index_") and f.endswith(".html"))
        for f in olds[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
    os.replace(tmp, path)


# ---------------------------- exe 镜像 ----------------------------
def download_exe(url, out_path, expect_size=0, prefixes=(), timeout=120, proxy=None):
    """下载单个 exe，返回 (是否成功, 信息)。已存在且大小相符则跳过。"""
    if os.path.exists(out_path) and expect_size and os.path.getsize(out_path) == expect_size:
        return True, "已存在且大小相符，跳过"
    if os.path.exists(out_path) and expect_size and os.path.getsize(out_path) != expect_size:
        try:
            os.remove(out_path)      # 残包，删掉重下
        except OSError:
            pass
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    last_err = ""
    for prefix in prefixes:
        target = prefix + url if prefix else url
        host = prefix or "github.com(直连)"
        try:
            op = opener(proxy)
            req = urllib.request.Request(target, headers={"User-Agent": UA})
            with op.open(req, timeout=timeout) as r:
                total = int(r.headers.get("Content-Length") or 0)
                tmp = out_path + ".part"
                got, last_pct = 0, -1
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 18)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        denom = total or expect_size
                        if denom:
                            pct = int(got * 100 / denom)
                            if pct >= last_pct + 25 or pct == 100:
                                last_pct = pct
                                log("    %s %d%%" % (os.path.basename(out_path), pct))
            size = os.path.getsize(tmp)
            if expect_size and size != expect_size:
                os.remove(tmp)
                last_err = "%s 大小不符（%d != %d）" % (host, size, expect_size)
                log("    !! " + last_err)
                continue
            os.replace(tmp, out_path)
            return True, "下载成功（%s，%s）" % (human(size), host)
        except Exception as e:
            last_err = "%s 失败：%s" % (host, e)
            log("    !! " + last_err)
            continue
    return False, last_err or "全部镜像源失败"


def prune_old_exes(ddir, current_names, keep_versions=2):
    """downloads/ 里只保留最近 keep_versions 个版本的 exe，其余删除。"""
    if not os.path.isdir(ddir):
        return []

    def ver_of(n):
        m = EXE_VER_RE.search(n)
        return m.group(1) if m else ""

    def key(v):
        return [int(x) for x in v.split(".") if x.isdigit()]

    vers = sorted({v for v in (ver_of(f) for f in os.listdir(ddir)) if v},
                  key=key, reverse=True)
    keep = set(vers[:max(1, keep_versions)])
    removed = []
    for f in os.listdir(ddir):
        v = ver_of(f)
        if not v or v in keep or f in current_names:
            continue
        try:
            os.remove(os.path.join(ddir, f))
            removed.append(f)
        except OSError:
            pass
    return removed


def rewrite_links(html, mirrored_names):
    """把页面里指向 GitHub Release 的下载链接改为本站相对路径。"""
    if not mirrored_names:
        return html, 0
    n = 0

    def rep(m):
        nonlocal n
        name = m.group(1)
        if name in mirrored_names:
            n += 1
            return "%s/%s" % (MIRROR_SUBDIR, name)
        return m.group(0)

    pat = re.compile(r"https://github\.com/%s/releases/(?:latest/download|download/[^/\s\"']+)/(InvoiceOcrTool_v\d+(?:\.\d+)+(?:_setup)?\.exe)"
                     % re.escape(OWNER_REPO))
    return pat.sub(rep, html), n


# ---------------------------- 主流程 ----------------------------
def main():
    ap = argparse.ArgumentParser(description="发票识别汇总工具 站点自动更新")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="网站根目录（默认=脚本所在目录）")
    ap.add_argument("--mirror-exe", action="store_true",
                    help="把双 exe 镜像到本站 downloads/，并把页面下载链接改为相对路径")
    ap.add_argument("--keep-exe", type=int, default=DEFAULT_KEEP_EXE_VERSIONS,
                    help="downloads/ 保留最近几个版本的 exe（默认 %d）" % DEFAULT_KEEP_EXE_VERSIONS)
    ap.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "",
                    help="HTTP 代理，如 http://127.0.0.1:7890（默认读环境变量）")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                    help="GitHub Token（可选，仅用于提高 API 速率限制）")
    ap.add_argument("--source-url", action="append", default=[],
                    help="追加/优先使用的页面来源（可多次传）")
    ap.add_argument("--exe-prefix", action="append", default=[],
                    help="exe 下载加速前缀，如 https://ghfast.top/（可多次传，会插到默认列表最前）")
    ap.add_argument("--force", action="store_true", help="内容未变化也强制重写")
    ap.add_argument("--check", action="store_true", help="只检查线上最新版本，不写任何文件")
    ap.add_argument("--no-json", action="store_true", help="不生成 version.json")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    proxy = args.proxy or None
    prefixes = list(args.exe_prefix) + [p for p in EXE_PREFIXES if p not in args.exe_prefix]
    os.makedirs(dest, exist_ok=True)
    log("站点目录：%s" % dest)
    if proxy:
        log("使用代理：%s" % proxy)

    # 1) 查最新 Release（拿不到也不致命，页面本身可独立更新）
    ver, assets, published = "", {}, ""
    try:
        ver, assets, published = get_release(proxy=proxy, token=args.token or None)
        log("线上最新版本：v%s（发布于 %s，资产 %d 个）" % (ver, published[:10] or "-", len(assets)))
    except Exception as e:
        log("!! 查询 Release 失败（不阻断页面更新）：%s" % e)

    if args.check:
        for n, a in assets.items():
            log("   资产 %s  %s" % (n, human(a["size"])))
        log("--check：仅检查，未写入任何文件")
        return 0

    # 2) 拉展示页
    try:
        data, src = fetch_page(proxy=proxy, extra_sources=args.source_url)
    except Exception as e:
        log("!! 拉取展示页失败：%s" % e)
        return 1
    text = data.decode("utf-8")
    page_ver = page_version(text)
    log("页面来源：%s（页面标注 v%s）" % (src, page_ver or "?"))

    # 3) 可选：镜像双 exe
    mirrored = {}
    if args.mirror_exe:
        if not assets:
            log("!! 未取到 Release 资产清单，跳过 exe 镜像")
        else:
            ddir = os.path.join(dest, MIRROR_SUBDIR)
            names = [n for n in assets if EXE_NAME_RE.search(n)]
            names.sort(key=lambda n: ("_setup" in n, n))
            log("开始镜像 %d 个 exe（共 %s）…"
                % (len(names), human(sum(assets[n]["size"] for n in names))))
            for n in names:
                ok, info = download_exe(assets[n]["url"], os.path.join(ddir, n),
                                        expect_size=assets[n]["size"],
                                        prefixes=prefixes, proxy=proxy)
                log("  %s -> %s" % (n, info))
                if ok:
                    mirrored[n] = assets[n]["size"]
            removed = prune_old_exes(ddir, set(mirrored), args.keep_exe)
            if removed:
                log("  清理旧版 exe：%s" % "、".join(removed))

    # 4) 改写下载链接（只对本站已镜像的文件）
    new_text, n_links = rewrite_links(text, set(mirrored))
    if n_links:
        log("已把 %d 处下载链接指向本站 %s/" % (n_links, MIRROR_SUBDIR))
    new_data = new_text.encode("utf-8")

    # 5) 幂等写入
    index = os.path.join(dest, "index.html")
    old_hash = ""
    if os.path.exists(index):
        old_hash = hashlib.sha256(open(index, "rb").read()).hexdigest()
    new_hash = hashlib.sha256(new_data).hexdigest()
    if old_hash == new_hash and not args.force:
        log("页面内容无变化，跳过写入")
    else:
        write_atomic(index, new_data, keep_backup=True,
                     backup_tag=("v" + page_ver) if page_ver else "")
        log("已更新 %s（%s，备份保留 %d 份于 %s/）"
            % (index, human(len(new_data)), BACKUP_KEEP, BACKUP_DIR_NAME))

    # 6) version.json（给前端/监控用）
    if not args.no_json:
        info = {
            "version": ver or page_ver,
            "page_version": page_ver,
            "release_published_at": published,
            "source": src,
            "mirrored_exe": mirrored,
            "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "repo": "https://github.com/" + OWNER_REPO,
            "pages": "https://jianry.github.io/invoice-ocr-tool/",
        }
        write_atomic(os.path.join(dest, "version.json"),
                     json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"),
                     keep_backup=False)
        log("已写入 version.json")

    log("完成：站点已同步到最新版本")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已中断")
        sys.exit(130)
