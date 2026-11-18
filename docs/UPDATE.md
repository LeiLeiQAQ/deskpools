# 官网、后台与排行榜一体化

官网、管理后台、安装包下载和排名协议由 `website/server.py` 一个进程提供，共用一个端口和域名。排名协议复用 `ranking_server/server.py` 中的 `RankingProtocol`，不需要另外运行排名服务。

## 本地启动

在仓库根目录执行：

```powershell
.\tools\run_local_backend.ps1
```

脚本会检查端口、等待数据库和服务就绪，并显示官网、后台、管理员令牌文件和日志路径。端口占用或进程退出时会报告错误。

- 官网：<http://127.0.0.1:8070/>
- 管理后台：<http://127.0.0.1:8070/admin>
- 服务状态：<http://127.0.0.1:8070/healthz>
- 排名数据：<http://127.0.0.1:8070/v1/leaderboard>

首次启动自动生成随机管理员令牌，保存在 `website/data/admin-token.txt`。打开该文件，把令牌粘贴到后台登录框。也可通过 `DIB_SITE_ADMIN_TOKEN` 设置固定令牌。浏览器仅在当前标签页的 sessionStorage 保存令牌，退出登录会清除。

一起启动使用该后台的游戏客户端：

```powershell
.\tools\run_local_backend.ps1 -LaunchClient -OpenBrowser
```

客户端进程的 `DIB_RANK_URL` 和 `DIB_WEBSITE_URL` 都指向该后台。启动前先关闭正在运行的游戏实例，以便新进程读取配置。

## 软件内检查更新

打开顶部工具条的「设置」，可查看当前版本号并点击「检查更新」。客户端使用 `DIB_WEBSITE_URL` 源地址下的 `GET /api/releases/latest`，读取后台标记为当前发布的版本。请求在后台线程执行，检查时按钮禁用，完成后显示最新状态；连接或数据错误会显示原因，详细信息记录在 `%TEMP%\DesktopIconBilliards\update.log`，可再次点击重试。

发现更高版本时显示版本号与更新说明，「去更新」使用默认浏览器打开后台发布的下载地址。支持站内 `/downloads/<filename>` 和完整 HTTPS 下载链接，不直接执行安装包或覆盖运行中的软件。只在用户点击时发起检查，启动不检查、不定时轮询；F1 隐藏期间收到结果也不会弹出窗口。

本机后台允许 `http://127.0.0.1:8070`、`http://localhost:8070` 或 IPv6 回环地址；公网地址使用 HTTPS。`DIB_WEBSITE_URL` 填写源地址，不带页面路径、查询参数或管理员令牌。

无需游戏依赖也能运行服务器，只需要 Python 3.11 或以上版本，在仓库根目录执行：

```powershell
python -m website.server --host 127.0.0.1 --port 8070
```

`python website/server.py` 同样受支持。服务使用 Python 标准库和 SQLite。

## 后台操作

### 下载点击统计

官网首页和版本卡片的下载按钮使用本站 `/download?version=版本号&source=hero`（版本卡片为 `source=release`）入口。服务器仅从已发布版本中读取下载地址，原子记录一次点击后返回 302 跳转；发布时可填写夸克网盘等 HTTPS 下载链接。直接打开 `/download` 会使用当前发布版本。

登录后台的「下载统计」可查看累计、今日、近 7 天、近 30 天、按版本、入口来源和近 14 天明细。数据在现有站点数据库的 `download_daily` 表中按北京时间（UTC+8）逐日累积，服务重启和版本切换不会清零。统计接口为需要管理员认证的 `GET /api/admin/downloads`。

这是下载入口点击量，不是完成下载次数或去重人数；直接访问夸克分享链接、客户端直接打开网盘、HEAD 检查和带预取标记的请求不会计入。页面浏览和读取版本接口也不计数。无需收集 IP、设备或浏览器标识，功能启用前的历史点击没有补录。

反向代理中明确设置 `proxy_cache off;` 与 `proxy_cache_convert_head off;`。宝塔可能在全局启用代理缓存，继承该配置会将 HEAD 请求转换成 GET；仅依靠应用返回 `Cache-Control: no-store` 并不足以保留请求方法。部署脚本已为本站禁用这两项，并用真实 HTTPS HEAD 请求核验。

### 发布 / 编辑版本

1. 在「版本发布」选择 Windows 安装包，支持 `.exe`、`.msi`、`.zip`。
2. 点击「上传选中的安装包」，上传成功后自动填入下载地址，并显示文件大小与 SHA-256 校验值。
3. 填写版本号、标题、更新说明和功能亮点，勾选是否设为当前下载版本。
4. 点击「保存并发布版本」，官网立即显示发布结果。
5. 在「版本记录」中编辑已有版本，或将旧版设为当前版本。

也可选择文件列表中的已有安装包，或填写完整的 HTTPS 下载链接。站内下载地址在发布前会检查对应文件是否存在，避免发布空链接。上传的文件名带内容摘要，重新上传不同文件不会覆盖已发布的下载链接。上传和下载都按块处理，避免把完整安装包常驻内存。

官网使用明确标记的当前版本，而不是把最近写入的任何版本当作最新版。保留旧版下载链接，支持手动切换当前版本。

### 排行榜管理

后台统计参赛玩家数、提交成绩数、最快平均用时和当前版本。成绩列表支持分页，显示连接 IP、Windows 系统版本、设备 ID、提交时间、用时、图标数与击球次数。

- 「隐藏成绩 / 恢复成绩」：调整一条成绩的有效性。
- 「停用设备 / 启用设备」：控制该设备是否计入榜单和继续提交成绩。

操作会同步影响官网和游戏中的榜单，每个设备取有效个人最佳，按 `total_ms / icon_count` 从小到大排序。榜单不填充示例玩家。排名仍校验 HMAC-SHA256 签名、请求时间戳、回合状态、重复提交、图标数和成绩范围；这些校验属于基本一致性检查，不是完整的游戏防作弊系统。

### 官网功能展示

「网站设置」可以编辑功能状态 JSON。它影响官网展示；新游戏能力随安装包发布，客户端保持手动更新，不恢复启动时的更新检测。

## 数据与部署

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `DIB_SITE_HOST` | `127.0.0.1` | 监听地址 |
| `DIB_SITE_PORT` | `8070` | 统一端口 |
| `DIB_SITE_ADMIN_TOKEN` | 自动生成到文件 | 管理员令牌 |
| `DIB_SITE_DB` | `website/data/site.db` | 版本和官网设置数据库 |
| `DIB_RANK_DB` | `ranking_server/data/rankings.db` | 排名数据库 |
| `DIB_SITE_DOWNLOAD_DIR` | `dist` | 安装包上传和下载目录 |
| `DIB_SITE_MAX_UPLOAD_MB` | `256` | 单文件上传上限（MiB） |

沿用已有数据库，不清空之前的发布和成绩。首次启动会给旧排名表补充成绩隐藏字段。备份时使用 SQLite backup API 或停服复制两个数据库，并同时保存安装包目录和管理员令牌。运行中的 WAL 文件也属于数据库状态，不能只复制主文件当作完整备份。

公网部署使用 HTTPS 反向代理，将整个域名转发到统一服务，不再单独配置排名端口。代理的请求体大小应覆盖安装包上限，上传超时也应覆盖实际上传时间。客户端默认的 `DIB_RANK_URL` 和 `DIB_WEBSITE_URL` 均为 `https://deskpools.com`，环境变量仍可覆盖地址以运行本地后台。

### deskpools.com 生产环境

官网：<https://deskpools.com/>；后台：<https://deskpools.com/admin>。现有宝塔站点保留原 HTTPS 证书，Nginx 将请求转发到 `127.0.0.1:8070`。

| 项目 | 路径或值 |
| --- | --- |
| 实际运行源码 | `/www/wwwroot/deskpools.com/`（实体目录，可在宝塔中直接编辑） |
| 官网与后台页面 | `/www/wwwroot/deskpools.com/website/templates/` |
| CSS / JavaScript | `/www/wwwroot/deskpools.com/website/static/` |
| 排行榜源码 | `/www/wwwroot/deskpools.com/ranking_server/` |
| 兼容入口 | `/opt/deskpools/current`（指向上述宝塔站点目录） |
| 发布归档 | `/opt/deskpools/releases/<UTC时间戳>/` |
| Python | `/opt/deskpools/venv/bin/python` |
| 托管服务 / 用户 | `deskpools.service` / `deskpools` |
| 生产配置 | `/etc/deskpools.env` |
| 网站 / 排名数据库 | `/var/lib/deskpools/site.db` / `/var/lib/deskpools/rankings.db` |
| 下载文件 | `/var/lib/deskpools/downloads/` |
| 管理员令牌 | `/var/lib/deskpools/admin-token.txt` |
| Nginx 反代扩展 | `/www/server/panel/vhost/nginx/extension/deskpools.com/deskpools-backend.conf` |
| 部署前备份 | `/var/backups/deskpools/<UTC时间戳>/` |
| 后续发布的源码备份 | `/www/wwwroot/.deskpools-source-backups/<UTC时间戳>/previous/` |

常用运维命令：

```sh
systemctl status deskpools.service
journalctl -u deskpools.service -n 100 --no-pager
curl --fail http://127.0.0.1:8070/healthz
systemctl restart deskpools.service
nginx -t
```

本地运行 `python -m tools.package_website` 生成不含数据库及凭据的部署包；将部署包与 `tools/deploy_deskpools.py` 上传后，在服务器执行 `python3 /tmp/deskpools-deploy.py /tmp/deskpools-0.3.0-server.tar.gz`。部署脚本保留数据库、管理员令牌及现有发布记录，备份旧配置和同名下载包，将源码实体更新到宝塔站点目录，再重启服务并验证健康接口，通过已认证接口发布与下载文件哈希一致的更新记录；站点的 `.well-known`、`.user.ini` 等宝塔文件保留。

在宝塔中修改 Python 源码后执行 `systemctl restart deskpools.service`。修改 HTML 模板后也重启服务；CSS / JavaScript 修改后刷新浏览器，缓存最长约 5 分钟。数据库与登录令牌仍存放在 `/var/lib/deskpools/`，不要将它们复制到公开的静态资源目录。

## 接口

公共接口：

- `GET /healthz`、`GET /api/health`：检查两个数据库连接和服务状态。
- `GET /api/releases/latest`：当前版本。
- `GET /api/releases?limit=20`：当前版本、发布记录和功能设置。
- `GET /api/features`：官网功能设置。
- `GET /download?version=0.3.1&source=hero`：记录下载点击并跳转到已发布地址；省略版本时使用当前版本。
- `GET /downloads/<filename>`：流式下载安装包。
- `POST /v1/device/register`：匿名设备注册。
- `POST /v1/round/start`、`POST /v1/round/finish`：签名开局与提交成绩。
- `GET /v1/leaderboard?limit=50`：公共榜单。
- `GET /v1/rankings`：公开榜单，附带签名时返回个人排名。

管理接口统一携带 `X-Admin-Token` 或 `Authorization: Bearer`：

- `GET /api/admin/overview`：统计信息。
- `GET /api/admin/files`：可用安装包。
- `GET /api/admin/downloads`：北京时间的下载点击汇总、按版本、入口与每日明细。
- `GET /api/admin/ranking?limit=25&offset=0`：分页成绩。
- `POST /api/admin/uploads`：原始二进制文件，`X-Filename` 携带 URL 编码后的原文件名。
- `POST /api/admin/releases`：发布或编辑版本，JSON 字段为 `version/title/notes/download_url/features/current`。
- `POST /api/admin/releases/current`：以 `{"version":"0.2.1"}` 切换当前版本。
- `POST /api/admin/features`：保存功能状态对象。
- `POST /api/admin/ranking/score`：以 `score_id`、`hidden` 管理成绩。
- `POST /api/admin/ranking/device`：以 `device_id`、`disabled` 管理设备。

前端位于 `website/templates` 和 `website/static`。旧独立排名入口仍保留供兼容部署，新部署使用上述统一后台即可。
