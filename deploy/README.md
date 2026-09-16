# 宝塔面板部署：站点自动更新脚本

本地 `release.py` 每次发版会把新版展示页推到 GitHub；这台服务器上的
`update_site.py` 负责把最新页面拉回来（可选把两个 exe 也镜像到本站）。

链路：

```
本地 release.py 发版  →  推 GitHub + Release 双 exe + 更新 docs/index.html(Pages)
                                    ↓
服务器 update_site.py（定时跑）  →  拉最新页面写入网站目录 + 可选镜像 exe
```

---

## 一、准备

| 项 | 要求 |
|---|---|
| Python | 3.7 及以上（宝塔自带 `/usr/bin/python3` 即可，**不需要装任何第三方包**） |
| 网站目录 | 例如 `/www/wwwroot/invoice-ocr-tool`（已建好站点、已绑域名） |
| 出网 | 服务器需能访问 GitHub 或 jsDelivr（拉不到时会自动换源，见「常见问题」） |

## 二、上传脚本

任选一种：

**方式 1：宝塔文件管理器**
把 `update_site.py` 上传到网站根目录（例如 `/www/wwwroot/invoice-ocr-tool/`）。

**方式 2：SSH 一条命令下载（推荐）**

```bash
cd /www/wwwroot/invoice-ocr-tool
curl -fsSL -o update_site.py https://cdn.jsdelivr.net/gh/jianRY/invoice-ocr-tool@main/deploy/update_site.py
curl -fsSL -o update_site.sh  https://cdn.jsdelivr.net/gh/jianRY/invoice-ocr-tool@main/deploy/update_site.sh
chmod +x update_site.sh
```

> jsDelivr 拉不动就换 GitHub 原始地址：
> `https://raw.githubusercontent.com/jianRY/invoice-ocr-tool/main/deploy/update_site.py`

## 三、先手动跑一次

```bash
cd /www/wwwroot/invoice-ocr-tool
python3 update_site.py --check          # 只看线上最新版本，不写文件
python3 update_site.py                  # 正式同步展示页
python3 update_site.py --mirror-exe     # 顺手把双 exe 镜像到本站 downloads/
```

正常输出长这样：

```
[2026-09-16 20:10:03] 站点目录：/www/wwwroot/invoice-ocr-tool
[2026-09-16 20:10:05] 线上最新版本：v1.1.0（发布于 2026-09-16，资产 2 个）
[2026-09-16 20:10:06] 页面来源：https://jianry.github.io/invoice-ocr-tool/index.html（页面标注 v1.1.0）
[2026-09-16 20:10:06] 已更新 .../index.html（26.3 KB，备份保留 5 份于 .site_backup/）
[2026-09-16 20:10:06] 已写入 version.json
[2026-09-16 20:10:06] 完成：站点已同步到最新版本
```

跑完打开你的域名，页面底部版本号应与 GitHub 上一致。

## 四、配置宝塔定时任务

宝塔面板 → **计划任务** → 添加任务：

| 字段 | 填什么 |
|---|---|
| 任务类型 | Shell 脚本 |
| 任务名称 | 同步发票工具站点 |
| 执行周期 | 每天 1 次（或每小时 1 次，脚本幂等，跑多了无副作用） |
| 脚本内容 | `bash /www/wwwroot/invoice-ocr-tool/update_site.sh --mirror-exe >> /www/wwwlogs/invoice-site.log 2>&1` |

日志看 `/www/wwwlogs/invoice-site.log`；脚本退出码 0 表示成功，非 0 会让宝塔任务显示失败。

## 五、参数一览

| 参数 | 说明 |
|---|---|
| `--dest 目录` | 网站根目录，**默认 = 脚本所在目录**（所以脚本放网站根目录时不用传） |
| `--mirror-exe` | 把双 exe 下到 `downloads/`，并把页面里 GitHub 下载链接改写为 `downloads/xxx.exe` |
| `--keep-exe N` | `downloads/` 保留最近几个版本的 exe（默认 2） |
| `--proxy URL` | 走代理，如 `--proxy http://127.0.0.1:7890`；默认读环境变量 `https_proxy` |
| `--token XXX` | GitHub Token，可选，仅用于提高 API 速率限制（默认读 `GITHUB_TOKEN`） |
| `--source-url URL` | 追加/优先的页面来源，可多次传 |
| `--exe-prefix URL` | exe 下载加速前缀，如 `https://ghfast.top/`，可多次传（会插到默认列表最前） |
| `--force` | 内容没变也强制重写 |
| `--check` | 只检查线上版本，不写任何文件 |
| `--no-json` | 不生成 `version.json` |

## 六、脚本做了什么（安全性）

- **内容校验**：拉回来的内容必须同时含 `id="appVer"`、`发票识别汇总工具`、`InvoiceOcrTool_v` 且大于 3KB，否则判为错误页/拦截页，**拒绝覆盖**你的站点。
- **原子写入**：先写 `.tmp` 再 `os.replace` 替换，最坏情况也不会留下半个文件。
- **自动备份**：每次覆盖前把旧 `index.html` 存到 `.site_backup/`，保留最近 5 份。
- **幂等**：内容哈希不变就跳过写入，定时任务反复跑没问题。
- **镜像校验**：exe 下完后按 GitHub API 报的字节数比对，大小不符当作残包删除重下。

## 七、常见问题

**1. 拉不到页面 / 全部来源失败**
服务器出网受限。三种解法：加 `--proxy http://127.0.0.1:7890`（本机有代理时）；
或用 `--source-url` 指定你能访问的镜像；或在自己的机器上把 `docs/index.html`
手工上传到网站目录（脚本只是让这件事自动化，不强制）。

**2. 想让页面下载链接指向自己的服务器**
加 `--mirror-exe`。它会把两个 exe 下到 `downloads/` 并把页面里的
`https://github.com/.../releases/latest/download/xxx.exe` 改成 `downloads/xxx.exe`，
国内用户直接从你的带宽下载。首次要下 ~170MB，之后同版本秒过。

**3. exe 下载太慢或卡住**
默认会依次尝试「直连 GitHub → ghfast.top → ghproxy.net → gh-proxy.com」，
全失败才算失败。想指定自己的加速站：`--exe-prefix https://你的加速站/`。

**4. 想换域名/换目录**
换绑域名后重跑即可，脚本不关心域名；换目录用 `--dest` 或把脚本挪过去。

**5. 页面更新了但浏览器还是旧的**
Nginx 缓存。宝塔站点设置里关掉「静态文件缓存」，或在 Nginx 配置加
`location = /index.html { add_header Cache-Control "no-cache"; }`。

**6. 会不会把我的站点改坏**
不会。校验不通过就不写；每次覆盖都留 5 份备份在 `.site_backup/`，
直接把备份文件改回 `index.html` 就回滚了。

**7. 之前用了 `--mirror-exe`，后来不带这个参数跑，链接会不会变回 GitHub**
会。页面是每次都从源站重新拉的原始版本，`--mirror-exe` 只影响当次是否改写链接。
所以一旦决定用本站镜像，计划任务里就一直带着 `--mirror-exe`（幂等，同版本不会重复下载）。
反过来也一样：不想要镜像就彻底去掉，`downloads/` 目录留着不影响页面。

## 八、目录结构（部署后）

```
/www/wwwroot/invoice-ocr-tool/
├─ index.html            # 展示页（脚本自动更新）
├─ version.json          # 当前版本信息（前端/监控可读）
├─ update_site.py        # 更新脚本
├─ update_site.sh        # 计划任务包装脚本
├─ downloads/            # --mirror-exe 时才有：镜像的双 exe
│   ├─ InvoiceOcrTool_v1.1.0.exe
│   └─ InvoiceOcrTool_v1.1.0_setup.exe
└─ .site_backup/         # 最近 5 份 index.html 备份
```
