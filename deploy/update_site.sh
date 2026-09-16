#!/bin/bash
# 发票识别汇总工具 —— 站点自动更新（宝塔面板「计划任务」用）
#
# 用法：
#   bash update_site.sh                    # 只同步展示页
#   bash update_site.sh --mirror-exe       # 同步展示页 + 镜像双 exe 到本站 downloads/
#   bash update_site.sh --dest /www/wwwroot/site --mirror-exe
#
# 宝塔计划任务里这样填（任务类型选 Shell 脚本）：
#   bash /www/wwwroot/invoice-ocr-tool/update_site.sh --mirror-exe >> /www/wwwlogs/invoice-site.log 2>&1

cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "未找到 python3。请在宝塔「软件商店」安装 Python 项目管理器，或把下面这行改成 python3 的绝对路径。"
  exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 开始同步 ====="
"$PY" update_site.py "$@"
code=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 结束，退出码 $code ====="
exit $code
