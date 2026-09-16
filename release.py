#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发票识别汇总工具 —— 一键发版脚本

一条命令跑完全链路：
  升版本号 -> 打包(单文件版 + 目录版) -> 签名 -> 编译安装版 -> 签名
  -> git commit/tag -> push -> 建 GitHub Release -> 上传双 exe
  -> 重新生成 docs/index.html(更新版本号与下载链接) -> push -> 复制到交付目录

用法：
  python release.py --bump patch        # 修 bug 类小改（1.0.0 -> 1.0.1）
  python release.py --bump minor        # 新增功能（1.0.0 -> 1.1.0）
  python release.py --bump major        # 大改版（1.0.0 -> 2.0.0）
  python release.py --version 1.2.3     # 直接指定版本号
  python release.py                     # 用当前 app.py 里的 VERSION 发版
  python release.py --dry-run           # 只本地打包+签名，不碰 git/远端
  python release.py --no-build          # 复用现有 exe，只做发布动作
  python release.py --no-installer      # 只发单文件版
  python release.py --no-push           # commit+tag 但只留本地

约定（建哥 2026-09-16 指令）：本工具每次有改动均自动发布新版本，
固定产出「单文件运行版 + 可安装版」两个 exe，并同步更新静态展示页。
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------- 常量 ----------------
ROOT = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(ROOT, "app.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
INDEX_HTML = os.path.join(ROOT, "docs", "index.html")
CACHE = os.path.join(ROOT, ".pybuild_cache")
DIST = os.path.join(ROOT, "dist")
DELIVERY = r"D:\workbuddy\杂项\发票识别汇总工具"

OWNER_REPO = "jianRY/invoice-ocr-tool"
REPO_URL = "https://github.com/" + OWNER_REPO
MAIN_BRANCH = "main"

# 含 tkinter 的构建 venv（managed venv 打不出 tkinter）
PY = r"C:/Users/toxuj/.workbuddy/binaries/python/envs/court_build_v13/Scripts/python.exe"
PYINSTALLER = r"C:/Users/toxuj/.workbuddy/binaries/python/envs/court_build_v13/Scripts/pyinstaller.exe"
GIT = r"C:\Users\toxuj\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin\git.exe"
WCRED = r"C:\Users\toxuj\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin\git-credential-wincred.exe"
SIGN_PY = r"D:\workbuddy\诉讼案件网站\.pybuild_cache\signing\sign.py"
PROXY = "http://127.0.0.1:10808"

ISCC_CANDIDATES = [
    r"C:\Users\toxuj\.workbuddy\tools\InnoSetup7\ISCC.exe",
    r"C:\Users\toxuj\.workbuddy\tools\InnoSetup6\ISCC.exe",
    r"D:\workbuddy\杂项\.workbuddy\invoice_tool\innosetup\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 7\ISCC.exe",
]

APP_NAME = "发票识别汇总工具"


def log(msg):
    print(">> " + msg, flush=True)


def run(cmd, cwd=None, check=True, env=None):
    p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if check and p.returncode != 0:
        print((p.stdout or "")[-1500:])
        print((p.stderr or "")[-1500:])
        raise SystemExit("命令失败(%d): %s" % (p.returncode, cmd[0]))
    return p


# ---------------- 版本号 ----------------
def read_version():
    s = open(APP_PY, encoding="utf-8").read()
    m = re.search(r'^VERSION\s*=\s*"([\d.]+)"', s, re.M)
    if not m:
        raise SystemExit("在 app.py 中找不到 VERSION 常量")
    return m.group(1)


def write_version(v):
    s = open(APP_PY, encoding="utf-8").read()
    s2 = re.sub(r'^VERSION\s*=\s*"[\d.]+"', 'VERSION = "%s"' % v, s, count=1, flags=re.M)
    if s2 == s:
        raise SystemExit("版本号写入失败")
    open(APP_PY, "w", encoding="utf-8").write(s2)


def bump(ver, kind):
    parts = [int(x) for x in ver.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if kind == "major":
        parts = [parts[0] + 1, 0, 0]
    elif kind == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    else:
        parts = [parts[0], parts[1], parts[2] + 1]
    return ".".join(str(x) for x in parts)


# ---------------- 构建 ----------------
def fresh_dir(name):
    d = os.path.join(CACHE, "%s_%s" % (name, time.strftime("%Y%m%d_%H%M%S")))
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def icon_path():
    p = os.path.join(ROOT, "assets", "app.ico")
    return p if os.path.exists(p) else None


def build_onefile():
    log("打包单文件版…")
    bld = fresh_dir("bld_onefile")
    dist = os.path.join(DIST, "onefile")
    os.makedirs(dist, exist_ok=True)
    cmd = [PYINSTALLER, "--onefile", "--windowed", "--noupx", "--name", "InvoiceOcrTool",
           "--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime",
           "--distpath", dist, "--workpath", bld, "--specpath", bld]
    ico = icon_path()
    if ico:
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "app.py"))
    p = run(cmd, cwd=ROOT, check=False)
    exe = os.path.join(dist, "InvoiceOcrTool.exe")
    if not os.path.exists(exe):
        print((p.stdout or "")[-2000:])
        print((p.stderr or "")[-2000:])
        raise SystemExit("单文件版构建失败")
    size = os.path.getsize(exe) / 1048576
    log("单文件版完成：%.1f MB" % size)
    if size < 60:
        raise SystemExit("产物仅 %.1f MB，疑似 tkinter/onnxruntime 未打进来，中止发布" % size)
    return exe


def build_onedir():
    log("打包目录版（安装包负载）…")
    bld = fresh_dir("bld_onedir")
    dist = os.path.join(DIST, "onedir")
    os.makedirs(dist, exist_ok=True)
    cmd = [PYINSTALLER, "--onedir", "--windowed", "--noupx", "--name", "InvoiceOcrTool",
           "--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime",
           "--distpath", dist, "--workpath", bld, "--specpath", bld]
    ico = icon_path()
    if ico:
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "app.py"))
    p = run(cmd, cwd=ROOT, check=False)
    d = os.path.join(dist, "InvoiceOcrTool")
    if not os.path.exists(os.path.join(d, "InvoiceOcrTool.exe")):
        print((p.stdout or "")[-2000:])
        raise SystemExit("目录版构建失败")
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(d) for f in fs)
    log("目录版完成：%.1f MB" % (total / 1048576))
    return d


def sign(exe, title):
    if not os.path.exists(SIGN_PY):
        log("!! 找不到签名脚本，跳过签名：" + SIGN_PY)
        return False
    log("签名：%s" % os.path.basename(exe))
    p = run([PY, SIGN_PY, exe, title, REPO_URL], check=False)
    ok = "Valid" in (p.stdout or "") or p.returncode == 0
    if not ok:
        print((p.stdout or "")[-800:])
    return ok


def build_installer(ver):
    iscc = next((p for p in ISCC_CANDIDATES if os.path.exists(p)), None)
    if not iscc:
        log("!! 未找到 ISCC.exe，跳过安装版")
        return None
    log("编译安装版…")
    out = os.path.join(DIST, "installer")
    os.makedirs(out, exist_ok=True)
    iss = os.path.join(ROOT, "installer.iss")
    p = run([iscc, "/DMyAppVersion=%s" % ver, "/O" + out, iss], cwd=ROOT, check=False)
    name = "InvoiceOcrTool_v%s_setup.exe" % ver
    exe = os.path.join(out, name)
    if not os.path.exists(exe):
        print((p.stdout or "")[-2000:])
        log("!! 安装版编译失败，继续发单文件版")
        return None
    log("安装版完成：%.1f MB" % (os.path.getsize(exe) / 1048576))
    return exe


# ---------------- 展示页 ----------------
def update_page(ver):
    if not os.path.exists(INDEX_HTML):
        return
    s = open(INDEX_HTML, encoding="utf-8").read()
    s = re.sub(r"InvoiceOcrTool_v[\d.]+(_setup)?\.exe",
               lambda m: "InvoiceOcrTool_v%s%s.exe" % (ver, m.group(1) or ""), s)
    s = re.sub(r'(id="appVer"[^>]*>)v[\d.]+(</span>)', r"\g<1>v%s\g<2>" % ver, s)
    s = re.sub(r'(<span class="ver">)v[\d.]+(</span>)', r"\g<1>v%s\g<2>" % ver, s)
    s = re.sub(r'(<span class="mock-title">%s )v[\d.]+(</span>)' % APP_NAME,
               r"\g<1>v%s\g<2>" % ver, s)
    open(INDEX_HTML, "w", encoding="utf-8").write(s)
    log("展示页已更新到 v%s" % ver)


# ---------------- GitHub ----------------
def get_token():
    f = os.path.join(CACHE, "token.txt")
    if os.path.exists(f):
        t = open(f, encoding="utf-8").read().strip()
        if t:
            return t
    out = subprocess.run([WCRED, "get"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, timeout=30).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            t = line[9:].strip()
            os.makedirs(CACHE, exist_ok=True)
            open(f, "w", encoding="utf-8").write(t)
            return t
    raise SystemExit("拿不到 GitHub token")


def api(path, token, data=None, method=None, timeout=120):
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    op = urllib.request.build_opener(proxy)
    h = {"Authorization": "Bearer " + token, "User-Agent": "curl/8",
         "Accept": "application/vnd.github+json"}
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=body,
                                 headers=h, method=method)
    return op.open(req, timeout=timeout)


def changelog_section(ver):
    s = open(CHANGELOG, encoding="utf-8").read()
    m = re.search(r"^## v%s[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(ver), s, re.M | re.S)
    return ("v%s" % ver) if not m else m.group(0).strip()


def create_release(token, ver, body):
    try:
        r = api("/repos/%s/releases/tags/v%s" % (OWNER_REPO, ver), token)
        d = json.load(r)
        log("Release v%s 已存在，复用 id=%s" % (ver, d["id"]))
        return d["id"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    log("创建 Release v%s…" % ver)
    r = api("/repos/%s/releases" % OWNER_REPO, token, {
        "tag_name": "v" + ver,
        "name": "%s v%s" % (APP_NAME, ver),
        "body": body,
        "draft": False, "prerelease": False,
    })
    return json.load(r)["id"]


def upload_asset(token, rid, path, name):
    size = os.path.getsize(path)
    log("上传资产 %s（%.1f MB）…" % (name, size / 1048576))
    proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    op = urllib.request.build_opener(proxy)
    with open(path, "rb") as f:
        blob = f.read()
    url = "https://uploads.github.com/repos/%s/releases/%s/assets?name=%s" % (OWNER_REPO, rid, name)
    for attempt in (1, 2, 3):
        try:
            req = urllib.request.Request(url, data=blob, method="POST", headers={
                "Authorization": "Bearer " + token, "User-Agent": "curl/8",
                "Content-Type": "application/octet-stream", "Content-Length": str(len(blob)),
            })
            d = json.load(op.open(req, timeout=1800))
            log("  ok -> %s (%.1f MB)" % (d["name"], d["size"] / 1048576))
            return True
        except Exception as e:
            log("  第 %d 次上传失败：%s" % (attempt, e))
            time.sleep(4)
    return False


# ---------------- git ----------------
def git(*args, check=True):
    return run([GIT] + list(args), cwd=ROOT, check=check)


def git_commit_tag_push(ver, token):
    log("git 提交 + 打 tag…")
    git("add", "-A")
    st = git("status", "--short").stdout.strip()
    if not st:
        log("  无文件变更，跳过 commit")
    else:
        git("commit", "-q", "-m", "release: v%s" % ver)
    git("tag", "-a", "v" + ver, "-m", "%s v%s" % (APP_NAME, ver), check=False)
    log("推送（走代理）…")
    git("config", "http.proxy", PROXY, check=False)
    git("config", "https.proxy", PROXY, check=False)
    try:
        url = "https://x-access-token:%s@github.com/%s.git" % (token, OWNER_REPO)
        print("   $ git push <token>@github.com/%s.git %s v%s" % (OWNER_REPO, MAIN_BRANCH, ver))
        p = run([GIT, "push", url, MAIN_BRANCH, "v" + ver], check=False)
        if p.returncode != 0:
            print((p.stderr or "")[-800:])
            raise SystemExit("push 失败")
        log("  推送完成")
    finally:
        git("config", "--unset", "http.proxy", check=False)
        git("config", "--unset", "https.proxy", check=False)


# ---------------- 交付 ----------------
def copy_delivery(onefile, installer, ver):
    os.makedirs(DELIVERY, exist_ok=True)
    pairs = [(onefile, "%s-单文件版.exe" % APP_NAME)]
    if installer:
        pairs.append((installer, "%s-安装版.exe" % APP_NAME))
    for src, nm in pairs:
        if src and os.path.exists(src):
            dst = os.path.join(DELIVERY, nm)
            shutil.copy2(src, dst)
            log("交付 -> %s" % dst)


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="发票识别汇总工具 一键发版")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打包+签名，不碰 git/远端")
    ap.add_argument("--no-build", action="store_true", help="复用现有 exe")
    ap.add_argument("--no-installer", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--allow-missing-notes", action="store_true")
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)

    cur = read_version()
    ver = args.version or (bump(cur, args.bump) if args.bump else cur)
    if ver != cur:
        write_version(ver)
        log("版本号：%s -> %s" % (cur, ver))
    else:
        log("版本号：v%s（未变更）" % ver)

    if not args.dry_run and not re.search(r"^## v%s" % re.escape(ver),
                                          open(CHANGELOG, encoding="utf-8").read(), re.M):
        if not args.allow_missing_notes:
            raise SystemExit("CHANGELOG.md 里没有 v%s 段落，先补上再发版（或加 --allow-missing-notes）" % ver)

    # 1) 构建
    onefile = os.path.join(DIST, "onefile", "InvoiceOcrTool.exe")
    installer = None
    if not args.no_build:
        onefile = build_onefile()
        onedir = build_onedir()
        sign(onefile, APP_NAME)
        if not args.no_installer:
            installer = build_installer(ver)
            if installer:
                sign(installer, APP_NAME + " 安装版")
    else:
        installer = os.path.join(DIST, "installer", "InvoiceOcrTool_v%s_setup.exe" % ver)

    copy_delivery(onefile, installer, ver)
    update_page(ver)

    if args.dry_run:
        log("--dry-run：不提交、不推送、不发 Release。产物在 dist/")
        return

    # 2) 发布
    token = get_token()
    git_commit_tag_push(ver, token)
    if args.no_push:
        log("--no-push：已 commit+tag，未推送")
        return

    rid = create_release(token, ver, changelog_section(ver))
    ok = upload_asset(token, rid, onefile, "InvoiceOcrTool_v%s.exe" % ver)
    if installer and os.path.exists(installer):
        ok = upload_asset(token, rid, installer, "InvoiceOcrTool_v%s_setup.exe" % ver) and ok
    log("Release 页面：%s/releases/tag/v%s" % (REPO_URL, ver))
    log("展示页：https://jianry.github.io/invoice-ocr-tool/")
    if not ok:
        raise SystemExit("有资产上传失败，请重跑 --no-build 补传")


if __name__ == "__main__":
    main()
