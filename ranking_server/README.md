# Desktop Icon Billiards 排名服务

官网后台 `website/server.py` 已经内置同一套 `/v1/*` 排名接口，生产环境推荐只部署一个后台进程：官网、发布后台、下载和排行榜共用一个 HTTPS 域名。当前文件仍可单独启动排名服务，用于兼容旧部署。

这是与客户端 `dib.rank.RankApi` 完全兼容的独立排名后端。服务只使用 Python 标准库和 SQLite，不需要 Flask、Node 或额外运行时。

## 启动

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe ranking_server\server.py --host 127.0.0.1 --port 8787
```

默认数据库：`ranking_server/data/rankings.db`。生产环境通过环境变量指定持久化位置：

```powershell
$env:DIB_RANK_DB = 'D:\data\desktop-icon-billiards\rankings.db'
$env:DIB_RANK_HOST = '0.0.0.0'
$env:DIB_RANK_PORT = '8787'
.\.venv\Scripts\python.exe ranking_server\server.py
```

客户端联调时，把排名地址设置为本地服务：

```powershell
$env:DIB_RANK_URL = 'http://127.0.0.1:8787'
.\run.cmd
```

公网部署时，使用 HTTPS 反向代理，并把 `DIB_RANK_URL` 设置为不带路径的 HTTPS 源地址，例如 `https://rank.example.com`。

## 接口

| 方法 | 路径 | 作用 | 鉴权 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | 健康检查 | 无 |
| `POST` | `/v1/device/register` | 注册匿名设备哈希 | 无 |
| `POST` | `/v1/round/start` | 创建 6 小时有效回合 | HMAC |
| `POST` | `/v1/round/finish` | 校验并保存成绩 | HMAC |
| `GET` | `/v1/leaderboard?limit=50` | 公共 TOP 榜 | 无 |
| `GET` | `/v1/rankings` | TOP 榜和当前设备最佳成绩 | HMAC |

签名算法与客户端一致：

```text
HMAC-SHA256(secret, METHOD + "\n" + PATH + "\n" + X-Timestamp + "\n" + SHA256(body))
```

服务端按每台电脑的个人最佳成绩排序，成绩指标为 `total_ms / icon_count`。客户端提交设备哈希、回合计时、图标数量、击球次数，以及当前 Windows 系统版本与构建号。服务器将观察到的连接 IP 与系统版本保存为成绩快照，官网、后台和游戏排行榜统一显示这两个字段。不上传桌面图标名称、截图或文件内容。历史成绩缺失的字段明确显示为未记录；迁移数据库不会补造旧 IP 或系统。

直接部署时从 TCP 连接读取 IP，忽略请求中的 IP 和未受信任的 `X-Forwarded-For`。使用 Nginx 等反向代理时，设置 `DIB_TRUSTED_PROXIES` 为代理的 IP/CIDR（逗号分隔），例如 `127.0.0.1/32,::1/128`，并由代理设置 `X-Forwarded-For`。服务端从右向左解析代理链，取第一个非受信任节点；不要把所有网络设为受信任。系统版本是客户端自报字段。IP 只用于显示，不作为电脑的唯一标识。

## 数据表

- `devices`：设备 ID、客户端已加盐的机器哈希、服务端密钥和状态。
- `rounds`：回合 ID、设备、创建时间、过期时间和完成时间。
- `scores`：总耗时、图标数量、击球次数、平均毫秒/颗和提交时间。

服务端限制：图标数 `15..45`、击球数 `1..10000`、回合最长 6 小时、平均每颗低于 250ms 的成绩返回 `too_fast`。

同分成绩按提交时间、成绩 ID 稳定排序；结算排名与榜单一致。本局排名仅与其他设备的有效最佳成绩比较，避免把自己的历史成绩再次算作竞争者。

## 沙盒检查

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe tools/verify_ranking_sandbox.py --keep-running
```

默认监听 `127.0.0.1:8072`，使用 `build/ranking-sandbox/<时间>/` 中的独立数据库，建立 60 名测试玩家，检查并发、排序、分页、成绩校验、后台管理和进程重启后的持久化。该脚本会只读核对 `8070` 的榜单，确认测试没有写入原榜单。端口已占用时，用 `--port` 指定空闲端口。

检查报告为该目录下的 `report.json`，沙盒后台令牌保存在 `admin-token.txt`。不加 `--keep-running` 时，检查完成会关闭沙盒服务。
