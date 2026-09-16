#!/bin/bash
# ============================================================
# 发票识别汇总工具 · 官网更新脚本（宝塔面板专用）
# ------------------------------------------------------------
# 用法（两步）：
#   1. 把下面 WEB_DIR 改成你的网站根目录（宝塔里站点对应的目录）
#   2. 宝塔面板 → 计划任务 → 添加任务 → 任务类型选「Shell 脚本」
#      把本脚本全部内容粘贴进去保存即可
#      · 想自动更新：执行周期随便设（比如每天凌晨 1 次）
#      · 想手动更新：点任务的「执行」按钮
# ------------------------------------------------------------
# 脚本做什么：
#   1) 从 GitHub 拉取最新展示页（三源容灾：Pages → raw → jsDelivr），
#      校验完整后先备份旧网页、再原子替换；
#   2) 默认同时把「单文件版 / 安装版」两个 exe 镜像到本站 downloads/，
#      并把页面里的下载链接改成服务器直链，国内用户下载不再走 GitHub
#      （不想镜像就把下面的 MIRROR_EXE 改成 0）；
#   3) 任何一步失败都保留原网页，绝不可能把网站搞挂。
# ============================================================
WEB_DIR="/www/wwwroot/invoice-ocr-tool"   # ←←← 改成你的网站根目录！

# ---------------------- 可调参数 ----------------------
FILE="index.html"           # 站点首页文件名
MIRROR_EXE=1                # 1=同时把双 exe 镜像到本站 downloads/；0=只更新网页
PROXY=""                    # 服务器需要代理才能出网时填，如 http://127.0.0.1:7890
KEEP_BAK=5                  # 网页备份保留份数
KEEP_EXE=2                  # downloads/ 里保留最近几个版本的 exe
# -----------------------------------------------------

OWNER_REPO="jianRY/invoice-ocr-tool"
BRANCH="main"
DDIR="$WEB_DIR/downloads"
TMP="$WEB_DIR/$FILE.tmp"
BAK_DIR="$WEB_DIR/_webbak"
MIN_HTML=3000               # 网页最小字节数（小于此判为错误页）
MIN_EXE=5000000             # exe 最小字节数（小于此判为错误页/残包）

[ -z "$PROXY" ] && PROXY="${https_proxy:-}"

CURL=(curl -fsSL --connect-timeout 10 --max-time 120)
[ -n "$PROXY" ] && CURL+=(-x "$PROXY")

rm -f "$TMP"
trap 'rm -f "$TMP"; rm -f "$DDIR"/*.part 2>/dev/null' EXIT

mkdir -p "$BAK_DIR" 2>/dev/null
echo "[$(date '+%F %T')] 开始更新官网网页 ..."
[ -n "$PROXY" ] && echo "  使用代理: $PROXY"

# ---------- 1. 拉取最新网页（三源容灾） ----------
OK=""
for URL in \
"https://jianry.github.io/invoice-ocr-tool/index.html" \
"https://raw.githubusercontent.com/$OWNER_REPO/$BRANCH/docs/index.html" \
"https://cdn.jsdelivr.net/gh/$OWNER_REPO@$BRANCH/docs/index.html"
do
echo "  尝试来源: $URL"
if "${CURL[@]}" -o "$TMP" "$URL"; then
# 校验：非空 + 是完整 HTML + 是本工具的页面
if [ -s "$TMP" ] && [ "$(wc -c < "$TMP")" -gt "$MIN_HTML" ] \
&& grep -q "<!DOCTYPE html>" "$TMP" \
&& grep -q 'id="appVer"' "$TMP" \
&& grep -q "发票识别汇总工具" "$TMP" \
&& grep -q "InvoiceOcrTool_v" "$TMP"; then
OK="$URL"
break
fi
echo "  内容校验不通过，换下一个源"
else
echo "  拉取失败，换下一个源"
fi
done
if [ -z "$OK" ]; then
echo "[$(date '+%F %T')] 更新失败：所有来源都不可用，保留原网页不变"
exit 1
fi
echo "  页面拉取成功，来源: $OK"

# 页面里标注的版本号（用于定位 exe）
PAGE_VER="$(grep -o 'InvoiceOcrTool_v[0-9][0-9.]*' "$TMP" | head -1 | sed 's/^InvoiceOcrTool_v//; s/\.*$//')"
[ -n "$PAGE_VER" ] && echo "  页面版本: v$PAGE_VER"

# ---------- 2. 镜像双 exe（可选） ----------
# 说明：只镜像「下载成功」的文件，成功一个才改写一个链接；
#       失败则页面链接保持 GitHub 原始地址，不影响用户下载。
MIRRORED=""
mirror_one() {
NAME="$1"
DEST="$DDIR/$NAME"
REL="https://github.com/$OWNER_REPO/releases/latest/download/$NAME"
# 远端大小：跟随重定向后取最后一个 content-length（取不到就不校验大小）
RSIZE="$("${CURL[@]}" -IL "$REL" 2>/dev/null | tr -d '\r' \
| awk 'tolower($1)=="content-length:"{v=$2} END{print v}')"
LSZ=0
[ -f "$DEST" ] && LSZ="$(wc -c < "$DEST" | tr -d ' ')"
# 已下过且校验通过就跳过（远端大小取不到时按本地文件自校验，避免重复下 170MB）
if [ -f "$DEST" ] && [ "$LSZ" -gt "$MIN_EXE" ] && [ "$(head -c 2 "$DEST")" = "MZ" ] \
&& { [ -z "$RSIZE" ] || [ "$LSZ" = "$RSIZE" ]; }; then
echo "  $NAME 已存在且校验通过，跳过"
MIRRORED="$MIRRORED $NAME"
return 0
fi
for PREFIX in "" "https://ghfast.top/" "https://ghproxy.net/" "https://gh-proxy.com/"; do
SRC="$PREFIX$REL"
HOST="${PREFIX:-github.com 直连}"
echo "  下载 $NAME ← $HOST"
rm -f "$DEST.part"
if "${CURL[@]}" --max-time 1800 -o "$DEST.part" "$SRC"; then
GOT="$(wc -c < "$DEST.part" | tr -d ' ')"
if [ -n "$RSIZE" ] && [ "$GOT" != "$RSIZE" ]; then
echo "    !! 大小不符（$GOT / 应为 $RSIZE），换下一个源"
continue
fi
if [ "$GOT" -lt "$MIN_EXE" ]; then
echo "    !! 文件过小（$GOT 字节），疑似错误页，换下一个源"
continue
fi
if [ "$(head -c 2 "$DEST.part")" != "MZ" ]; then
echo "    !! 不是有效的 exe 文件，换下一个源"
continue
fi
mv -f "$DEST.part" "$DEST"
echo "    完成（$GOT 字节）"
MIRRORED="$MIRRORED $NAME"
return 0
else
echo "    !! 下载失败，换下一个源"
fi
done
rm -f "$DEST.part"
echo "  !! $NAME 全部源失败，跳过（该文件下载链接保持 GitHub 原地址）"
return 1
}

if [ "$MIRROR_EXE" = "1" ] && [ -n "$PAGE_VER" ]; then
mkdir -p "$DDIR" 2>/dev/null
echo "[$(date '+%F %T')] 镜像双 exe 到 $DDIR ..."
mirror_one "InvoiceOcrTool_v${PAGE_VER}.exe"
mirror_one "InvoiceOcrTool_v${PAGE_VER}_setup.exe"
fi

# ---------- 3. 改写下载链接为本站直链 ----------
MIRRORED="${MIRRORED# }"
if [ -n "$MIRRORED" ]; then
for NAME in $MIRRORED; do
sed -i "s|https://github.com/$OWNER_REPO/releases/[^\"' ]*/$NAME|downloads/$NAME|g" "$TMP"
done
echo "[$(date '+%F %T')] 已把下载链接指向本站 downloads/ （$MIRRORED）"
else
echo "  未镜像 exe，下载链接保持 GitHub 原始地址"
fi

# ---------- 4. 清理 downloads/ 里的旧版本 exe ----------
if [ -d "$DDIR" ]; then
VERS="$(ls -1 "$DDIR" 2>/dev/null | grep -o 'InvoiceOcrTool_v[0-9][0-9.]*' \
| sed 's/^InvoiceOcrTool_v//; s/\.*$//' | sort -Vru | head -n "$KEEP_EXE")"
for f in "$DDIR"/InvoiceOcrTool_v*.exe; do
[ -e "$f" ] || continue
b="$(basename "$f")"
keep=0
for v in $VERS; do
if [ "$b" = "InvoiceOcrTool_v$v.exe" ] || [ "$b" = "InvoiceOcrTool_v${v}_setup.exe" ]; then
keep=1
break
fi
done
[ "$keep" = "1" ] && continue
rm -f "$f" && echo "  清理旧版 exe: $b"
done
fi

# ---------- 5. 备份旧网页（保留最近 N 份） ----------
if [ -f "$WEB_DIR/$FILE" ]; then
cp -f "$WEB_DIR/$FILE" "$BAK_DIR/$FILE.$(date +%Y%m%d_%H%M%S)"
ls -1t "$BAK_DIR/$FILE."* 2>/dev/null | tail -n +$((KEEP_BAK + 1)) | xargs -r rm -f
fi

# ---------- 6. 原子替换 ----------
mv -f "$TMP" "$WEB_DIR/$FILE"
chmod 644 "$WEB_DIR/$FILE" 2>/dev/null
OK_SHA="$(md5sum "$WEB_DIR/$FILE" 2>/dev/null | cut -d' ' -f1)"

# ---------- 7. 写 version.json（给监控/前端读，可选） ----------
printf '{\n  "version": "v%s",\n  "page_version": "v%s",\n  "mirrored_exe": "%s",\n  "source": "%s",\n  "updated_at": "%s",\n  "repo": "https://github.com/%s",\n  "pages": "https://jianry.github.io/invoice-ocr-tool/"\n}\n' \
"$PAGE_VER" "$PAGE_VER" "${MIRRORED:-无}" "$OK" "$(date '+%F %T')" "$OWNER_REPO" \
> "$WEB_DIR/version.json" 2>/dev/null

echo "[$(date '+%F %T')] 更新成功！来源: $OK"
echo "  网页文件: $WEB_DIR/$FILE （MD5 $OK_SHA）"
echo "  历史备份: $BAK_DIR （保留最近 $KEEP_BAK 份）"
[ -n "$MIRRORED" ] && echo "  exe 镜像: $DDIR （保留最近 $KEEP_EXE 个版本）"
exit 0
