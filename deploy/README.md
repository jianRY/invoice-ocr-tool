# 宝塔面板部署：官网自动更新脚本

本地 `release.py` 每次发版会把新版展示页推到 GitHub；服务器上的
`update_site.sh`（纯 bash 单文件）负责把最新页面拉回来，并可选把两个 exe
镜像到本站，让国内用户直接从你的服务器下载。

```
本地 release.py 发版  →  推 GitHub + Release 双 exe + 更新 docs/index.html(Pages)
                                    ↓
服务器 update_site.sh（定时跑）  →  拉最新页面写入站点 + 镜像双 exe + 改写下载链接
```

---

## 一、准备

| 项 | 要求 |
|---|---|
| Shell | bash（宝塔默认就是），只需 `curl` —— **不需要 Python，也不需要装任何东西** |
| 网站目录 | 例如 `/www/wwwroot/invoice-ocr-tool`（已建好站点、已绑域名） |
| 出网 | 服务器需能访问 GitHub 或 jsDelivr（拉不到会自动换源，见「常见问题」） |

## 二、把脚本放上去

**方式 1：直接粘贴（最简单）**
宝塔 → 计划任务 → 添加任务 → 任务类型选 **Shell 脚本** → 把 `update_site.sh`
的全部内容粘进去即可，脚本不依赖自身路径。

**方式 2：SSH 下载到服务器（便于以后更新脚本本身）**

```bash
cd /www/wwwroot/invoice-ocr-tool
curl -fsSL -o update_site.sh https://cdn.jsdelivr.net/gh/jianRY/invoice-ocr-tool@main/deploy/update_site.sh
chmod +x update_site.sh
```

## 三、改一个变量

打开脚本，把第一行配置改掉：

```bash
WEB_DIR="/www/wwwroot/invoice-ocr-tool"   # ←←← 改成你的网站根目录！
```

其他参数都在紧随其后的「可调参数」区，默认值一般不用动：

| 变量 | 默认 | 说明 |
|---|---|---|
| `FILE` | `index.html` | 站点首页文件名 |
| `MIRROR_EXE` | `1` | `1`=同时把双 exe 镜像到本站 `downloads/` 并改写页面下载链接；`0`=只更新网页 |
| `PROXY` | 空 | 服务器需要代理才能出网时填，如 `http://127.0.0.1:7890`；留空则读环境变量 `https_proxy` |
| `KEEP_BAK` | `5` | 网页备份保留份数 |
| `KEEP_EXE` | `2` | `downloads/` 里保留最近几个版本的 exe |

## 四、先手动跑一次

```bash
bash /www/wwwroot/invoice-ocr-tool/update_site.sh
```

正常输出长这样：

```
[2026-09-16 20:10:03] 开始更新官网网页 ...
  尝试来源: https://jianry.github.io/invoice-ocr-tool/index.html
  页面拉取成功，来源: https://jianry.github.io/invoice-ocr-tool/index.html
  页面版本: v1.1.1
[2026-09-16 20:10:05] 镜像双 exe 到 /www/wwwroot/invoice-ocr-tool/downloads ...
  下载 InvoiceOcrTool_v1.1.1.exe ← ghfast.top
  ...
[2026-09-16 20:11:12] 已把下载链接指向本站 downloads/ （ InvoiceOcrTool_v1.1.1.exe InvoiceOcrTool_v1.1.1_setup.exe）
[2026-09-16 20:11:12] 更新成功！来源: https://jianry.github.io/invoice-ocr-tool/index.html
  网页文件: /www/wwwroot/invoice-ocr-tool/index.html （MD5 4c1b...）
  历史备份: /www/wwwroot/invoice-ocr-tool/_webbak （保留最近 5 份）
  exe 镜像: /www/wwwroot/invoice-ocr-tool/downloads （保留最近 2 个版本）
```

跑完打开你的域名，页面显示的版本号应与 GitHub 上一致，且点「下载」走的是你自己的服务器。

## 五、配置宝塔定时任务

宝塔面板 → **计划任务** → 添加任务：

| 字段 | 填什么 |
|---|---|
| 任务类型 | Shell 脚本 |
| 任务名称 | 同步发票工具官网 |
| 执行周期 | 每天 1 次（或每小时 1 次，脚本幂等，跑多了无副作用） |
| 脚本内容 | `bash /www/wwwroot/invoice-ocr-tool/update_site.sh >> /www/wwwlogs/invoice-site.log 2>&1` |

不想让日志文件无限长大，就把上面那行日志部分去掉，改用宝塔自带的「任务日志」查看输出。

退出码 `0` = 成功（含无更新）；非 0 会让宝塔任务显示失败，便于发现问题。

## 六、脚本做了什么（安全性）

- **三源容灾**：页面依次尝试 GitHub Pages → raw.githubusercontent → jsDelivr，
  任一成功即用；exe 依次尝试 直连 GitHub → ghfast.top → ghproxy.net → gh-proxy.com。
- **内容校验**：拉回来的页面必须 > 3KB，且同时含 `<!DOCTYPE html>`、`id="appVer"`、
  `发票识别汇总工具`、`InvoiceOcrTool_v`，否则判为错误页/拦截页，**拒绝覆盖**你的站点。
- **exe 校验**：下完必须「大小 == 远端 Content-Length」（能取到时）＋「> 5MB」＋「开头是 MZ」，
  三者都过才落地，残包直接丢弃换源。
- **只镜像成功的文件**：某个 exe 下载失败时，页面里它的链接保持 GitHub 原地址，
  不会出现「链接指向服务器但文件不存在」的死链。
- **原子替换**：先写 `index.html.tmp` 再 `mv` 覆盖，任何时刻都不会留下半个文件。
- **自动备份**：每次覆盖前把旧 `index.html` 存到 `_webbak/`，保留最近 5 份，回滚只需改个名。
- **幂等**：同版本的 exe 已存在且大小相符就跳过下载，定时任务反复跑没问题。

## 七、常见问题

**1. 拉不到页面 / 三个源全失败**
服务器出网受限。三种解法：在脚本里填 `PROXY`（本机有代理时）；或自己把 `docs/index.html`
手工上传到网站目录（脚本只是让这件事自动化，不强制）。

**2. 想让页面下载链接指向自己的服务器**
保持 `MIRROR_EXE=1`。脚本会把两个 exe 下到 `downloads/`，并把页面里的
`https://github.com/.../releases/latest/download/xxx.exe` 改成 `downloads/xxx.exe`。
首次要下 ~170MB，之后同版本秒过。

**3. exe 下载太慢或卡住**
默认四个源依次尝试，全失败才算失败。想用自己的加速站，就在脚本的
`for PREFIX in ...` 那行里把地址加到最前面。

**4. 之前镜像过，后来把 MIRROR_EXE 改成 0，链接会变回 GitHub 吗**
会。页面每次都从源站重新拉原始版本，`MIRROR_EXE` 只影响当次是否改写链接。
一旦决定用本站镜像，就一直保持 `MIRROR_EXE=1`（幂等，同版本不会重复下载）。
反过来也一样：`downloads/` 目录留着不影响页面。

**5. 页面更新了但浏览器还是旧的**
Nginx 缓存。宝塔站点设置里关掉「静态文件缓存」，或在 Nginx 配置加
`location = /index.html { add_header Cache-Control "no-cache"; }`。

**6. 会不会把我的站点改坏**
不会。校验不通过就不写；每次覆盖都留 5 份备份在 `_webbak/`，
把备份文件复制回 `index.html` 就完成回滚。

**7. 我已经把脚本粘贴进宝塔了，以后脚本本身更新了怎么办**
脚本是自包含的，不需要跟随仓库更新；真要拿最新版，重跑一遍「二、方式 2」的
下载命令，或重新粘贴一次。

## 八、目录结构（部署后）

```
/www/wwwroot/invoice-ocr-tool/
├─ index.html            # 展示页（脚本自动更新）
├─ version.json          # 当前版本信息（前端/监控可读）
├─ update_site.sh        # 更新脚本（纯 bash，单文件）
├─ downloads/            # MIRROR_EXE=1 时才有：镜像的双 exe
│   ├─ InvoiceOcrTool_v1.1.1.exe
│   └─ InvoiceOcrTool_v1.1.1_setup.exe
├─ _webbak/              # 最近 5 份 index.html 备份
```

> 附：`update_site.py` 是同一件事的 Python 进阶版（多 `--check`/`--proxy`/`--token`
> 参数与 `version.json` 精细输出），需要 Python 3；日常用 `update_site.sh` 就够了。
