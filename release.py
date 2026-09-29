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
  python release.py --no-verify-page    # 跳过发版后的线上展示页校验

约定：本工具每次有改动均自动发布新版本，无需等指令。
每次发版要走完下面三步（本脚本已全部固化，别绕过）：

  1) 网页同步 —— 展示页内容必须与本版数据一致：版本号、两个下载链接、页脚版本
     全部刷到新版本。服务器上的 deploy/update_site.sh（宝塔计划任务）是靠页面里的
     `InvoiceOcrTool_v<版本>` 拼出 Release 文件名去镜像 exe 的，所以
     「页面版本号 == git tag == Release 资产名版本」三者必须严格一致；
     有一个没跟上，宝塔脚本就会去下不存在的文件（404 后保留 GitHub 原链接）。
     update_page() 的硬校验（残留旧版本号即中止发版）就是守这条线，不要删。

  2) 双 exe 且都要自签名 —— 每次固定产出「单文件运行版 + 可安装版」两个 exe，
     打包后一律用 jianRY 证书签名（SHA256 + 时间戳），未签名的产物不发。

  3) 发版推送 —— commit + tag + push 到 GitHub、建 Release 并上传两个 exe
     （资产名 ASCII：InvoiceOcrTool_vX.Y.Z.exe / _setup.exe）；
     版本号按实际情况递增：小改 --bump patch、新功能 --bump minor、大改 --bump major。

服务器侧用 deploy/update_site.sh（纯 bash 单文件）定时同步展示页并镜像双 exe。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import devconfig   # 同目录模块：本机配置读取（环境变量 -> local_config.json -> 默认值）

# ---------------- 常量 ----------------
ROOT = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(ROOT, "app.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
INDEX_HTML = os.path.join(ROOT, "docs", "index.html")
CACHE = os.path.join(ROOT, ".pybuild_cache")
DIST = os.path.join(ROOT, "dist")

# ---------------- 本机专属配置（不入库） ----------------
# 仓库里**只放相对路径与通用默认值**：凡与开发机绑定的绝对路径
# （构建用 venv、签名脚本、Inno Setup、交付目录…）一律走 devconfig 的取值链：
#     ① 环境变量                          —— 临时覆盖，优先级最高
#     ② .pybuild_cache/local_config.json   —— 本机长期配置，已被 .gitignore 排除
#     ③ 下面的通用默认值 / 相对路径        —— 换台机器也能直接跑
# 目的：公开仓库里搜不到任何开发机信息（用户名、目录结构、其他项目位置）。
# 各配置项含义见 local_config.example.json。
_cfg = devconfig.get

# 交付目录（两个 exe + 使用说明一起放）
# 默认 = 源码目录的兄弟目录「杂项/发票识别汇总工具」（本项目交付位置本来就是这样）。
DELIVERY = _cfg("OCRTOOL_DELIVERY", "delivery_dir",
                os.path.normpath(os.path.join(ROOT, os.pardir, "杂项", "发票识别汇总工具")))

OWNER_REPO = "jianRY/invoice-ocr-tool"
REPO_URL = "https://github.com/" + OWNER_REPO
MAIN_BRANCH = "main"

# 备用下载源（地址放本机配置 .pybuild_cache/local_config.json，见 devconfig）：
#   /files/<资产名>      —— 双 exe 由服务器定时脚本从 Release 镜像过来
#   /updates/<app>.json  —— 由本脚本生成的 docs/update.json 抄过去
# 客户端自动更新优先读它，读不到再回退 GitHub（raw → API）。
SITE_URL = _cfg("OCRTOOL_SITE_URL", "site_url", "")     # 本机配置，仓库里不留真地址
SERVER_FILES = SITE_URL + "/files"
APP_KEY = "ocr"
UPDATE_JSON = os.path.join(ROOT, "docs", "update.json")

# 构建用的解释器：默认用「当前正在跑本脚本的解释器」——发版时本来就是拿那个
# 带 tkinter 的 venv 运行。⚠️ 本机请务必在 local_config.json 里写 "python"：
# 打包要求该解释器带 tkinter，缺了会打出没有界面的废件。
PY = _cfg("OCRTOOL_PY", "python", sys.executable)
PYINSTALLER = _cfg("OCRTOOL_PYINSTALLER", "pyinstaller",
                   os.path.join(os.path.dirname(PY),
                                "pyinstaller.exe" if os.name == "nt" else "pyinstaller"))
def _pick_git():
    """挑一个真能用的 git。

    坑（2026-09-16 实测）：PortableGit 的 git.exe 把 git-remote-https.exe 放在
    mingw64\\bin（不在 exec-path 里），git 靠翻 PATH 找远程 helper。本机 shell 的
    PATH 被裁剪过，PortableGit 的 bin 不一定在里面 —— 于是 push 报
    `git: 'remote-https' is not a git command`；就算把 PATH 补上也只是变成
    **静默失败**（returncode 128、零输出），更难看懂。
    系统 Git（2.55）helper 布局正常，实测可直接推送，所以优先用它。
    """
    for c in (r"C:\Program Files\Git\cmd\git.exe",
              r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if os.path.exists(c):
            return c
    # 兜底：从 PATH 找。不要把便携版路径写死 —— 那会把开发机用户名带进公开仓库。
    return shutil.which("git") or "git"


GIT = _pick_git()


def _pick_wincred():
    """从系统 Git 的安装位置推导 wincred helper —— 不写死任何本机路径。

    系统 Git 布局固定：<git_root>\\cmd\\git.exe
                 与 <git_root>\\mingw64\\libexec\\git-core\\git-credential-wincred.exe
    """
    if not GIT:
        return ""
    cand = os.path.join(os.path.dirname(os.path.dirname(GIT)),
                        "mingw64", "libexec", "git-core",
                        "git-credential-wincred.exe")
    return cand if os.path.exists(cand) else ""


# 取 PAT 的 helper（wincred）：默认从系统 Git 位置推导
WCRED = _cfg("OCRTOOL_WCRED", "wincred", _pick_wincred())

# 代码签名脚本：默认在项目自己的 .pybuild_cache/signing/ 下找；
# 本机若用外部共享的签名脚本，在 local_config.json 写 "sign_py" 指过去。
SIGN_PY = _cfg("OCRTOOL_SIGN_PY", "sign_py",
               os.path.join(CACHE, "signing", "sign.py"))
# ⚠️ 代理不再硬编码（2026-09-22 重构）：
#    原先写死 127.0.0.1:10808，该代理一关或换端口，发版就会卡在最后一步 git push 上
#    —— 打包/签名/元数据全做完了才失败，最亏的一步。
#    实际踩过：10808 端口还开着（TCP 能连）但已不能出网，git 报
#    「Could not connect to server」，而同一时刻环境变量里的代理是好的。
#    现在由 pick_proxy() 在发版开始时**实测**挑选，None 表示直连。
PROXY = None

# Inno Setup 编译器：本机安装位置走配置，仓库里只留通用安装路径。
ISCC_CANDIDATES = ([_cfg("OCRTOOL_ISCC", "iscc")] +
                   [r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                    r"C:\Program Files\Inno Setup 7\ISCC.exe",
                    r"C:\Program Files\Inno Setup 6\ISCC.exe"])

# ---------------- 敏感信息闸门（提交前扫描暂存内容） ----------------
# ⚠️ 本仓库是公开的：开发机信息/凭据一旦推上去，历史就洗不掉（要 force push
#    重写历史、所有协作者重新 clone）。所以命中时是**中止发版**，不是警告。
SENSITIVE_RULES = (
    (r"[Cc]:[\\/]+Users[\\/]+[A-Za-z0-9_.\-]{2,}", "本机用户目录"),
    (r"(?i)[a-z]:[\\/]+workbuddy[\\/]", "本地开发盘路径"),
    (r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b", "GitHub 令牌"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥内容"),
    (r"(?i)\b(password|passwd|pwd|secret|api[_-]?key)\s*[=:]\s*[\"'][^\"'\s]{8,}[\"']",
     "疑似硬编码凭据"),
)
# 不该出现在公开仓库里的文件名
SENSITIVE_PATHS = (r"(?i)\.(pfx|pvk|pem|key)$", r"(?i)token\.txt$",
                   r"(?i)local_config\.json$", r"(?i)^\.env")
# 放行：模板 / 占位符（不是真值）
SENSITIVE_ALLOW = (r"x-access-token:%s@", r"[Cc]:[\\/]+Users[\\/]+<")

APP_NAME = "发票识别汇总工具"


def log(msg):
    print(">> " + msg, flush=True)


def run(cmd, cwd=None, check=True, env=None, timeout=None):
    try:
        p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit("命令超时(%ss)：%s" % (timeout, cmd[0]))
    if check and p.returncode != 0:
        print((p.stdout or "")[-1500:])
        print((p.stderr or "")[-1500:])
        raise SystemExit("命令失败(%d): %s" % (p.returncode, cmd[0]))
    return p


def proxy_candidates():
    """按优先级给出代理候选；None 表示「不用代理、直连」。

    ① 环境变量 RELEASE_PROXY —— 显式指定，最高优先级；**空串表示强制直连**
    ② 本机代理软件常用端口（10808 / 10809 / 7890 / 7897）
    ③ 环境变量里的 https_proxy / http_proxy
    ④ None（直连）

    为什么环境变量代理排在**后面**：这类变量常由沙箱/其他工具注入，可能只放行了
    api.github.com，对 github.com 的 CONNECT 隧道会拒（2026-09-22 实测：环境里的
    127.0.0.1:62577 访问 api 返回 200、访问 github.com 返回 000），拿它 push 必挂。
    本机代理软件（Clash 等）的端口才是真正能出网的通道。
    """
    explicit = os.environ.get("RELEASE_PROXY")
    if explicit is not None:
        return [explicit.strip() or None]

    out = []
    for port in (10808, 10809, 7890, 7897):
        u = "http://127.0.0.1:%d" % port
        if u not in out:
            out.append(u)
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        v = (os.environ.get(key) or "").strip()
        if v and v not in out:
            out.append(v)
    out.append(None)
    return out


def proxy_works(proxy, timeout=5):
    """实测该代理能否访问 GitHub；proxy=None 时测的是直连。

    ⚠️ 两个域名**都必须通**才算可用：
      · api.github.com —— 建 Release、传资产走它
      · github.com     —— git push 走它（CONNECT 隧道到 443）
    踩过的坑：某个代理只放行 api.github.com，于是被判为「可用」，
    打包/签名/元数据全做完，最后 git push 报 CONNECT tunnel failed 502。
    """
    for url in ("https://api.github.com", "https://github.com"):
        try:
            handlers = ([urllib.request.ProxyHandler({"http": proxy, "https": proxy})]
                        if proxy else [urllib.request.ProxyHandler({})])
            op = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(url, headers={"User-Agent": "release-probe"})
            with op.open(req, timeout=timeout) as resp:
                if getattr(resp, "status", 0) != 200:
                    return False
        except Exception:  # noqa: BLE001
            return False
    return True


def pick_proxy(log_fn=None):
    """挑第一个**实测能用的**代理；全不行就直连。"""
    for cand in proxy_candidates():
        if proxy_works(cand):
            if log_fn:
                log_fn("代理：%s" % (cand or "不使用（直连）"))
            return cand
    if log_fn:
        log_fn("代理：候选全部不可用，按直连处理")
    return None


def git_env(proxy=None):
    """git 子进程环境：补齐 PATH，并把代理统一成 proxy 参数指定的那一个。

    为什么要显式清掉环境变量里的代理：
      ① git config 的 http.proxy 优先级**高于**环境变量，残留一个失效代理会让
         push 直接失败（2026-09-22 踩过，见 PROXY 上方注释）；
      ② 环境变量里的代理是别人注入的，未必能出网。
      所以一律清空，再由 proxy 参数写入唯一的一个，行为可预期。

    PATH 部分：这个 git 是便携版，它把 git-remote-https.exe 放在 mingw64\\bin 下
    （不在 exec-path 里），git 找远程 helper 时会翻 PATH。外部 PATH 被裁剪过
    （本环境 shell 的 PATH 常缺 Git 目录）时就报
    `git: 'remote-https' is not a git command`，push 直接失败。显式补上。
    """
    e = dict(os.environ)
    e["PATH"] = os.path.dirname(GIT) + os.pathsep + e.get("PATH", "")
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        e.pop(k, None)
    if proxy:
        e["http_proxy"] = e["https_proxy"] = proxy
        e["HTTP_PROXY"] = e["HTTPS_PROXY"] = proxy
    return e


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
def build_env():
    """打包/签名子进程用的环境：剥掉外部注入的 PYTHONPATH。

    开发沙箱（WorkBuddy 等）会通过 PYTHONPATH 注入 sitecustomize.py，给
    shutil.rmtree / os.remove 挂上「安全删除」钩子。PyInstaller 覆盖 dist 里的
    旧产物时会踩到钩子，于是**打包静默失败、留下上一版 exe**，而脚本还以为是
    新包（症状：体积和上版一模一样、时间戳是旧的）。
    剥掉 PYTHONPATH 后子进程不再加载那层钩子，PyInstaller 行为与命令行直跑一致。
    """
    e = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONSTARTUP"):
        e.pop(k, None)
    return e


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
    cmd = [PYINSTALLER, "--onefile", "--windowed", "--noupx", "--noconfirm",
           "--name", "InvoiceOcrTool",
           "--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime",
           "--collect-all", "pymupdf",
           "--distpath", dist, "--workpath", bld, "--specpath", bld]
    ico = icon_path()
    if ico:
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "app.py"))
    exe = os.path.join(dist, "InvoiceOcrTool.exe")
    t0 = time.time()
    p = run(cmd, cwd=ROOT, check=False, env=build_env())
    if p.returncode != 0 or not os.path.exists(exe):
        print((p.stdout or "")[-3000:])
        print((p.stderr or "")[-3000:])
        raise SystemExit("单文件版构建失败（PyInstaller 返回码 %s）" % p.returncode)
    if os.path.getmtime(exe) < t0 - 2:
        print((p.stdout or "")[-3000:])
        raise SystemExit("单文件版产物是旧的（PyInstaller 未真正重建），中止发布")
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
    cmd = [PYINSTALLER, "--onedir", "--windowed", "--noupx", "--noconfirm",
           "--name", "InvoiceOcrTool",
           "--collect-all", "rapidocr_onnxruntime", "--collect-all", "onnxruntime",
           "--collect-all", "pymupdf",
           "--distpath", dist, "--workpath", bld, "--specpath", bld]
    ico = icon_path()
    if ico:
        cmd += ["--icon", ico]
    cmd.append(os.path.join(ROOT, "app.py"))
    d = os.path.join(dist, "InvoiceOcrTool")
    t0 = time.time()
    p = run(cmd, cwd=ROOT, check=False, env=build_env())
    target = os.path.join(d, "InvoiceOcrTool.exe")
    if p.returncode != 0 or not os.path.exists(target):
        print((p.stdout or "")[-3000:])
        print((p.stderr or "")[-3000:])
        raise SystemExit("目录版构建失败（PyInstaller 返回码 %s）" % p.returncode)
    if os.path.getmtime(target) < t0 - 2:
        print((p.stdout or "")[-3000:])
        raise SystemExit("目录版产物是旧的（PyInstaller 未真正重建），中止发布")
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(d) for f in fs)
    log("目录版完成：%.1f MB" % (total / 1048576))
    return d


def verify_exe(exe):
    """用打包产物跑一次无界面自检，确认包内的 PDF 渲染库真的可用。

    防的是「打包漏收 mupdf 运行库」这类问题：本地跑得好好的，用户装完
    一处理 PDF 就报错。自检只验证「能启动 + 能把 PDF 转成 JPG」，不跑 OCR。
    """
    log("校验 exe 内置 PDF 转换能力…")
    # 用带时间戳的新目录，不做任何删除操作：开发沙箱给 os.remove/shutil.rmtree 挂了
    # 安全删除钩子，批量删除会直接终止进程
    d = os.path.join(CACHE, "selftest_%s" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(d, exist_ok=True)
    gen = ("import pymupdf, os; d=r'%s'; doc=pymupdf.open(); pg=doc.new_page();"
           "pg.insert_text((72,72),'selftest');"
           "doc.save(os.path.join(d,'selftest.pdf')); doc.close()" % d)
    run([PY, "-c", gen], env=build_env())
    run([exe, "--selftest", d], cwd=d, check=False, timeout=900)
    logf = os.path.join(d, "selftest.log")
    text = open(logf, encoding="utf-8").read() if os.path.exists(logf) else ""
    for line in (text.strip() or "(没有生成 selftest.log)").splitlines():
        log("  | " + line)
    if "RESULT=OK" not in text:
        raise SystemExit("exe 自检未通过：包内 PDF 转换不可用，中止发版"
                         "（确认无碍可加 --skip-exe-check）")
    log("exe 自检通过：包内 PDF 转换可用")


def sign(exe, title):
    if not os.path.exists(SIGN_PY):
        log("!! 找不到签名脚本，跳过签名：" + SIGN_PY)
        return False
    log("签名：%s" % os.path.basename(exe))
    p = run([PY, SIGN_PY, exe, title, REPO_URL], check=False)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    ok = p.returncode == 0 and "签名完成" in out
    if not ok:
        print(out[-900:])
        log("!! 签名未成功，继续发布（请检查证书）")
    else:
        log("  签名 ok")
    return ok


def build_installer(ver):
    iscc = next((p for p in ISCC_CANDIDATES if os.path.exists(p)), None)
    if not iscc:
        log("!! 未找到 ISCC.exe，跳过安装版")
        return None
    payload = os.path.join(DIST, "onedir", "InvoiceOcrTool")
    if not os.path.exists(os.path.join(payload, "InvoiceOcrTool.exe")):
        log("!! 目录版负载不存在（%s），跳过安装版" % payload)
        return None
    log("编译安装版…")
    outdir = os.path.join(DIST, "installer")
    os.makedirs(outdir, exist_ok=True)
    iss = os.path.join(ROOT, "installer.iss")
    p = run([iscc, "/DMyAppVersion=%s" % ver, "/O" + outdir, iss], cwd=ROOT, check=False)
    name = "InvoiceOcrTool_v%s_setup.exe" % ver
    exe = os.path.join(outdir, name)
    if not os.path.exists(exe):
        txt = (p.stdout or "") + "\n" + (p.stderr or "")
        errs = [l for l in txt.splitlines()
                if re.search(r"error|Error|Error |not found|cannot|Cannot|无效|找不到", l)]
        print("\n".join(errs[-14:]) if errs else txt[-1500:])
        log("!! 安装版编译失败，继续发单文件版")
        return None
    log("安装版完成：%.1f MB" % (os.path.getsize(exe) / 1048576))
    return exe


# ---------------- 展示页 ----------------
def page_versions(html):
    """提取页面里所有版本号标记（文件名 / 正文 / 页脚 / 窗口标题）。"""
    pats = [
        r"_v(\d+\.\d+\.\d+)",                                     # 资产文件名
        r'id="appVer"[^>]*>\s*v?(\d+\.\d+\.\d+)',                 # Hero 当前版本
        r'<span class="ver">\s*v?(\d+\.\d+\.\d+)',                # 页脚版本
        r'<span class="mock-title">[^<]*?v(\d+\.\d+\.\d+)',       # 窗口标题栏
    ]
    found = set()
    for p in pats:
        found.update(re.findall(p, html))
    return found


def update_page(ver):
    """把展示页的版本号与下载链接刷到 ver；替换不生效则中止发版（不再静默通过）。"""
    if not os.path.exists(INDEX_HTML):
        raise SystemExit("找不到展示页 %s，无法同步更新" % INDEX_HTML)
    s0 = open(INDEX_HTML, encoding="utf-8").read()
    s = s0
    s = re.sub(r"InvoiceOcrTool_v[\d.]+(_setup)?\.exe",
               lambda m: "InvoiceOcrTool_v%s%s.exe" % (ver, m.group(1) or ""), s)
    s = re.sub(r'(id="appVer"[^>]*>)v[\d.]+', r"\g<1>v%s" % ver, s)
    s = re.sub(r'(<span class="ver">)v[\d.]+', r"\g<1>v%s" % ver, s)
    s = re.sub(r'(<span class="mock-title">%s )v[\d.]+' % APP_NAME,
               r"\g<1>v%s" % ver, s)

    # 硬校验：页面上出现的每一处版本号都必须是新版本
    if 'id="appVer"' not in s:
        raise SystemExit("展示页缺少 id=\"appVer\" 标记，页面结构可能已改动")
    stale = sorted(page_versions(s) - {ver})
    if stale:
        raise SystemExit("展示页仍残留旧版本号 %s，已中止发版（请检查 docs/index.html 的版本标记）"
                         % stale)
    if s == s0:
        log("展示页无变化（已是 v%s）" % ver)
    else:
        open(INDEX_HTML, "w", encoding="utf-8").write(s)
        log("展示页已更新到 v%s" % ver)
    return True


def check_page_names(html, ver):
    """校验页面里出现的下载文件名就是本版资产名。

    服务器上的 deploy/update_site.sh 是按页面里的 `InvoiceOcrTool_v<版本>`
    拼出 releases/latest/download/<名字> 去镜像 exe 的；页面名字与 Release 资产名
    对不上就会 404，而且表现是"静默退回 GitHub 原链接"，不报错、极易漏掉。
    """
    want = {"InvoiceOcrTool_v%s.exe" % ver, "InvoiceOcrTool_v%s_setup.exe" % ver}
    got = set(re.findall(r"InvoiceOcrTool_v[\d.]+(?:_setup)?\.exe", html))
    if got == want:
        log("页面下载文件名与资产名一致：%s" % "、".join(sorted(got)))
        return True
    log("!! 页面里的下载文件名与本版不一致 —— 页面 %s / 期望 %s"
        % (sorted(got) or ["无"], sorted(want)))
    log("   宝塔脚本会按页面版本拼文件名，不一致会导致镜像失败（退回 GitHub 链接）")
    return False


def verify_assets(ver, rid, token, pairs):
    """硬校验：Release 上的资产名与大小必须与本地产物一致，不一致直接中止。

    这是「保证宝塔脚本能自动更新到新版网页」的关键一环 ——
    名字错一个字节，服务器那边就是 404，而且不报错。
    """
    d = json.load(api("/repos/%s/releases/%s/assets" % (OWNER_REPO, rid), token))
    have = {a["name"]: a["size"] for a in d}
    bad = []
    for path, name in pairs:
        if name not in have:
            bad.append("缺少资产 %s" % name)
        elif os.path.exists(path) and have[name] != os.path.getsize(path):
            bad.append("资产 %s 大小不符（线上 %d / 本地 %d）"
                       % (name, have[name], os.path.getsize(path)))
    if bad:
        raise SystemExit("Release 资产校验失败：\n  " + "\n  ".join(bad))
    log("Release 资产校验通过：%s" % "、".join(n for _, n in pairs))
    return set(have)


def verify_page(ver, wait=180):
    """发版后校验线上 GitHub Pages 是否已生效（Pages 重建需要时间）。失败只告警。"""
    url = "https://jianry.github.io/invoice-ocr-tool/"
    t0 = time.time()
    log("校验线上展示页（最多等 %ds）…" % wait)
    while time.time() - t0 < wait:
        try:
            proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
            op = urllib.request.build_opener(proxy)
            req = urllib.request.Request(
                url + "?nocache=%d" % int(time.time()),
                headers={"User-Agent": "curl/8", "Cache-Control": "no-cache"})
            with op.open(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            got = sorted(page_versions(html))
            if got == [ver]:
                log("线上展示页已更新到 v%s（耗时 %.0fs）" % (ver, time.time() - t0))
                check_page_names(html, ver)
                return True
            log("  线上版本为 %s，等待 Pages 重建…" % (got or ["?"]))
        except Exception as e:
            log("  校验请求失败：%s" % e)
        time.sleep(20)
    log("!! 线上展示页在 %ds 内未更新到 v%s（Pages 构建可能较慢，请稍后访问确认）" % (wait, ver))
    return False



# ---------------- 更新元数据（客户端自动更新用） ----------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_update_json(ver, onefile, installer):
    """生成 docs/update.json —— 客户端自动更新的数据源（随 commit 推到 Pages）。

    ⚠️ 字段语义 2026-09-22 调换过，别改回去：
      url          = GitHub Release 直链（**主源**，客户端会再展开成各加速镜像择优）
      fallback_url = 备用源直链（**兜底**，只在镜像与原站都不可用时才用）

    为什么从「服务器优先」调回「GitHub 优先」：
      实测裸网直连 GitHub 只有 3.7 KB/s，而公共加速镜像能到 439 KB/s ——
      比备用源（阿里云 ECS 固定带宽，445 KB/s 封顶）不慢，还不用维护同步。
    调换后新旧客户端都能正确工作：两边都是「按 url → fallback_url 顺序试」。

    另带 sha256 / size，客户端下载完校验完整性，杜绝半截包当新版装上。
    """
    src = onefile if (onefile and os.path.exists(onefile)) else None
    asset = "InvoiceOcrTool_v%s.exe" % ver
    data = {
        "app": APP_KEY,
        "name": APP_NAME,
        "version": ver,
        "asset": asset,
        "notes": changelog_section(ver),
        "url": "%s/releases/download/v%s/%s" % (REPO_URL, ver, asset),
        "release_url": "%s/releases/tag/v%s" % (REPO_URL, ver),
        "size": os.path.getsize(src) if src else 0,
        "sha256": _sha256(src) if src else "",
        "published": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # ⚠️ 不写入 fallback_url / site_url：update.json 是**随 Release 公开的资产**，
    #    把私有更新地址写进去等于把它公开。客户端只走 url（GitHub 直链）+ 加速镜像。
    if installer and os.path.exists(installer):
        sn = os.path.basename(installer)
        data["setup_url"] = "%s/releases/download/v%s/%s" % (REPO_URL, ver, sn)
    os.makedirs(os.path.dirname(UPDATE_JSON), exist_ok=True)
    with open(UPDATE_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log("更新元数据 -> docs/update.json（v%s，%s）"
        % (ver, "含 sha256" if data["sha256"] else "无 sha256"))
    return data


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
    # 同名资产先删除，保证脚本可重入
    try:
        d = json.load(api("/repos/%s/releases/%s/assets" % (OWNER_REPO, rid), token))
        for a in d:
            if a["name"] == name:
                r = api("/repos/%s/releases/assets/%s" % (OWNER_REPO, a["id"]),
                        token, method="DELETE")
                r.read()
                log("  已删除同名旧资产 %s" % name)
    except Exception as e:
        log("  同名资产检查失败（忽略）：%s" % e)
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
    return run([GIT] + list(args), cwd=ROOT, check=check, env=git_env(PROXY))


def scan_staged_sensitive():
    """扫描暂存内容，命中开发机信息/凭据就返回告警列表（空列表 = 干净）。

    只看 diff 的新增行（`+` 开头），避免被历史内容反复报错；
    文件名单独查一遍，防止 *.pfx / token.txt / local_config.json 被误加进来。
    """
    hits = []
    names = run([GIT, "diff", "--cached", "--name-only"],
                check=False, env=git_env(PROXY)).stdout or ""
    for n in names.splitlines():
        n = n.strip().strip('"')
        if n and any(re.search(p, n) for p in SENSITIVE_PATHS):
            hits.append(("不该入库的文件", n))
    diff = run([GIT, "diff", "--cached", "-U0"],
               check=False, env=git_env(PROXY)).stdout or ""
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if any(re.search(a, body) for a in SENSITIVE_ALLOW):
            continue
        for pat, label in SENSITIVE_RULES:
            for m in re.finditer(pat, body):
                hits.append((label, m.group(0)[:70]))
    return hits


def git_commit_tag_push(ver, token):
    log("git 提交 + 打 tag…")
    git("add", "-A")
    # ⚠️ 公开仓库的硬闸门：提交前扫一遍，命中就停。宁可这次不发，也不漏出去。
    if not os.environ.get("OCRTOOL_SKIP_SCAN"):
        hits = scan_staged_sensitive()
        if hits:
            log("!! 暂存内容命中敏感信息，已中止发版（共 %d 处）：" % len(hits))
            for label, seg in hits[:20]:
                log("   · %s -> %s" % (label, seg))
            if len(hits) > 20:
                log("   … 另有 %d 处" % (len(hits) - 20))
            log("   修掉后重新发版；确认是误报可临时设 OCRTOOL_SKIP_SCAN=1 跳过。")
            raise SystemExit("敏感信息闸门拦截")
        log("  敏感信息闸门：通过")
    st = git("status", "--short").stdout.strip()
    if not st:
        log("  无文件变更，跳过 commit")
    else:
        git("commit", "-q", "-m", "release: v%s" % ver)
    git("tag", "-a", "v" + ver, "-m", "%s v%s" % (APP_NAME, ver), check=False)
    log("推送%s…" % ("（经 %s）" % PROXY if PROXY else "（直连）"))
    # ⚠️ 先清掉仓库里可能残留的 http.proxy：git config 的优先级**高于**环境变量，
    #    残留一个已失效的代理会直接让 push 失败（2026-09-22 踩过：10808 端口还开着
    #    但出不了网，打包签名全成功、最后一步 push 挂掉）。
    #    本次用哪个代理完全由 git_env(PROXY) 注入的环境变量决定。
    git("config", "--unset", "http.proxy", check=False)
    git("config", "--unset", "https.proxy", check=False)
    try:
        url = "https://x-access-token:%s@github.com/%s.git" % (token, OWNER_REPO)
        print("   $ git push <token>@github.com/%s.git %s v%s" % (OWNER_REPO, MAIN_BRANCH, ver))
        p = run([GIT, "push", url, MAIN_BRANCH], check=False, env=git_env(PROXY))
        if p.returncode != 0:
            print(re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", p.stderr or "")[-800:])
            log("  提示：可显式指定代理重试 —— set RELEASE_PROXY=http://127.0.0.1:7890")
            log("        或强制直连 —— set RELEASE_PROXY=")
            raise SystemExit("push 分支失败")
        p2 = run([GIT, "push", url, "v" + ver], check=False, env=git_env(PROXY))
        if p2.returncode != 0:
            log("  !! tag v%s 推送失败（可能远端已存在），继续" % ver)
        log("  推送完成")
    finally:
        git("config", "--unset", "http.proxy", check=False)
        git("config", "--unset", "https.proxy", check=False)


# ---------------- 交付 ----------------
# 「下载（推荐）」置顶区块：发版时自动补/刷新，保证交付出去的使用说明永远带备用源地址。
#
# ⚠️ 区块内容里**绝不能出现 "MB" 字样**：本脚本用
#    re.sub(r"[\d.]+ MB", …, count=len(sizes)) 刷 exe 体积，只替换前 N 处匹配；
#    置顶块若也写了 MB，体积数字就会被刷到错的位置上去。
SERVER_MARK = "【下载（推荐：国内直连，速度快）】"
SERVER_BLOCK = (
    SERVER_MARK + "\n"
    + SITE_URL + "/\n"
    "本站为国内服务器直链，不必访问 GitHub；GitHub 地址见文末【在线资源】。\n\n"
)
_SERVER_BLOCK_RE = re.compile(
    r"^【下载（推荐[^\n]*】\n[^\n]*\n[^\n]*\n\n", re.M)


def ensure_server_top(manual):
    """把「下载（推荐）」区块置顶到使用说明（幂等：已存在则整块替换为最新）。"""
    t = open(manual, encoding="utf-8").read()
    if _SERVER_BLOCK_RE.search(t):
        t2 = _SERVER_BLOCK_RE.sub(lambda m: SERVER_BLOCK, t, count=1)
    else:
        t2 = SERVER_BLOCK + t
    if t2 != t:
        open(manual, "w", encoding="utf-8", newline="").write(t2)
        log("交付 -> 使用说明.txt（已置顶下载地址 %s）" % (SITE_URL + "/"))
    return True


def copy_delivery(onefile, installer, ver):
    os.makedirs(DELIVERY, exist_ok=True)
    # ⚠️ 交付文件名必须带版本号（定稿约定）：放桌面/U 盘一眼看出版本，
    #    不会新旧两个文件名一样分不清。GitHub Release 资产名保持 ASCII（同步链依赖）。
    pairs = [(onefile, "%s_v%s_单文件版.exe" % (APP_NAME, ver))]
    if installer:
        pairs.append((installer, "%s_v%s_安装版.exe" % (APP_NAME, ver)))
    # 旧命名（不带版本 / 旧版本号）的残留归档到 _旧版备份/，避免和新名字并存混淆
    import glob as _glob
    legacy_dir = os.path.join(DELIVERY, "_旧版备份")
    new_names = {nm for _, nm in pairs}          # 本次要写的目标名：直接覆盖，不归档
    for legacy in ("%s-单文件版.exe" % APP_NAME, "%s-安装版.exe" % APP_NAME,
                   "%s_v[0-9.]*_单文件版.exe" % APP_NAME,
                   "%s_v[0-9.]*_安装版.exe" % APP_NAME):
        for p in _glob.glob(os.path.join(DELIVERY, legacy)):
            if os.path.basename(p) in new_names:
                continue                          # 同名 = 本版产物，稍后 copy2 覆盖即可
            try:
                os.makedirs(legacy_dir, exist_ok=True)
                shutil.move(p, os.path.join(legacy_dir, os.path.basename(p)))
                log("交付 -> 旧命名归档 %s" % os.path.basename(p))
            except (OSError, shutil.Error) as e:
                log("!! 旧命名归档失败（忽略）：%s（%s）" % (os.path.basename(p), e))
    sizes = []
    for src, nm in pairs:
        if src and os.path.exists(src):
            dst = os.path.join(DELIVERY, nm)
            shutil.copy2(src, dst)
            sizes.append(os.path.getsize(dst) / 1048576.0)
            log("交付 -> %s" % dst)
    if os.path.exists(INDEX_HTML):
        shutil.copy2(INDEX_HTML, os.path.join(DELIVERY, "展示页.html"))
        log("交付 -> %s" % os.path.join(DELIVERY, "展示页.html"))
    # 部署脚本一并交付（宝塔服务器用），避免每次手工同步漏掉
    dep_src = os.path.join(ROOT, "deploy")
    dep_dst = os.path.join(DELIVERY, "宝塔部署脚本")
    if os.path.isdir(dep_src):
        os.makedirs(dep_dst, exist_ok=True)
        for nm in ("update_site.sh", "update_site.py", "README.md"):
            f = os.path.join(dep_src, nm)
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(dep_dst, nm))
        log("交付 -> %s（update_site.sh / update_site.py / README.md）" % dep_dst)
    # 使用说明.txt：版本号与 exe 体积自动跟着发版走，避免手工漏改
    manual = os.path.join(DELIVERY, "使用说明.txt")
    if os.path.exists(manual):
        t = open(manual, encoding="utf-8").read()
        t2 = re.sub(r"^发票识别汇总工具 v[\d.]+ 使用说明",
                    "发票识别汇总工具 v%s 使用说明" % ver, t, count=1, flags=re.M)
        t2 = re.sub(r"^v[\d.]+(\s+)\d{4}-\d{2}-\d{2}",
                    "v%s\\g<1>" % ver + time.strftime("%Y-%m-%d"), t2, count=1, flags=re.M)
        # 交付文件名带版本号后，说明里的两处文件名引用也要同步刷：
        # 旧命名「…-单文件版.exe」（连字符）与新命名「…_vX.Y.Z_单文件版.exe」都认
        t2 = re.sub(r"%s(?:-|_v[\d.]+_)单文件版\.exe" % re.escape(APP_NAME),
                    "%s_v%s_单文件版.exe" % (APP_NAME, ver), t2)
        t2 = re.sub(r"%s(?:-|_v[\d.]+_)安装版\.exe" % re.escape(APP_NAME),
                    "%s_v%s_安装版.exe" % (APP_NAME, ver), t2)
        it = iter("%.1f MB" % s for s in sizes)
        t2 = re.sub(r"[\d.]+ MB", lambda m: next(it, m.group(0)), t2, count=len(sizes))
        if t2 != t:
            open(manual, "w", encoding="utf-8", newline="").write(t2)
            log("交付 -> 使用说明.txt（已刷到 v%s）" % ver)
        # 置顶「下载（推荐）」区块（放最后，不受上面体积替换影响）
        ensure_server_top(manual)


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="发票识别汇总工具 一键发版")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打包+签名，不碰 git/远端")
    ap.add_argument("--no-build", action="store_true", help="复用现有 exe")
    ap.add_argument("--skip-exe-check", action="store_true",
                    help="跳过打包产物的 PDF 自检")
    ap.add_argument("--no-installer", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-verify-page", action="store_true", help="跳过发版后的线上展示页校验")
    ap.add_argument("--verify-wait", type=int, default=180, help="线上展示页校验等待秒数（默认 180）")
    ap.add_argument("--allow-missing-notes", action="store_true")
    args = ap.parse_args()

    global PROXY
    PROXY = pick_proxy(log)

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
        if not args.skip_exe_check:
            verify_exe(onefile)
        sign(onefile, APP_NAME)
        if not args.no_installer:
            installer = build_installer(ver)
            if installer:
                sign(installer, APP_NAME + " 安装版")
    else:
        installer = os.path.join(DIST, "installer", "InvoiceOcrTool_v%s_setup.exe" % ver)

    # 展示页先刷新，再复制到交付目录（保证离线副本与线上一致）
    update_page(ver)
    write_update_json(ver, onefile, installer)
    copy_delivery(onefile, installer, ver)

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
    pairs = [(onefile, "InvoiceOcrTool_v%s.exe" % ver)]
    if installer and os.path.exists(installer):
        pairs.append((installer, "InvoiceOcrTool_v%s_setup.exe" % ver))
    ok = upload_asset(token, rid, pairs[0][0], pairs[0][1])
    for path, name in pairs[1:]:
        ok = upload_asset(token, rid, path, name) and ok
    # 更新元数据也作为 Release 资产上传：服务器定时脚本从
    # releases/latest/download/update.json 抄到站点 /updates/<app>.json，
    # 客户端自动更新优先读那份。上传失败只告警，不中止发版（客户端会退回 GitHub API）。
    if not upload_asset(token, rid, UPDATE_JSON, "update.json"):
        log("!! update.json 上传失败 —— 自动更新仍可用，但检查会退回 GitHub API（较慢）")
    log("Release 页面：%s/releases/tag/v%s" % (REPO_URL, ver))
    log("展示页：https://jianry.github.io/invoice-ocr-tool/")
    if not ok:
        raise SystemExit("有资产上传失败，请重跑 --no-build 补传")
    # 硬校验资产名与大小：宝塔脚本按名拼下载地址，错了会静默失效
    verify_assets(ver, rid, token, pairs)

    # 3) 校验线上展示页已同步（Pages 重建需要时间，失败只告警）
    if not args.no_verify_page:
        verify_page(ver, args.verify_wait)


if __name__ == "__main__":
    main()
