# 0.3.0 优化与验收记录

实施与本机验收：2026-11-17；线上复核：2026-11-18（UTC+8）。范围：本地 Windows 客户端、官网、统一后台与排名协议。本记录区分实测结果与未覆盖项目，不代表所有 Windows 设备均已验证。

## 已完成的优化

- 统一可见球径、碰撞圆与辅助线接触判定；修正竞技台面小数坐标导致的 Pillow 启动错误。
- 统一 F1、任务栏与托盘的暂停恢复；支持快捷键修改、冲突提示、右键取消蓄力和重开确认。
- 静音与音量可记忆，击打和落袋采用 pygame-ce 异步音频；切到其他应用时隐藏附属窗口并停止声音。
- 界面、动画双心跳由独立进程监测，失联后终止本次游戏并恢复已记录的桌面状态。
- 自由练习、固定 15 球标准排位、90 秒挑战和每日挑战；三种竞技模式暂停继续计时，球杆皮肤使用相同物理参数。
- 真人、电脑和旧版分榜，竞技玩法独立计分；修正同序号新台面切换后误用旧局次及过期网络响应覆盖新局的问题。
- SQLite 保存最近十局与待提交队列，停止参与会取消队列；同一局次的重试由服务端幂等处理。
- 服务端增加局次时间约束；90 秒挑战个人最佳按进球数、用时和杆数判定。
- 构建版本、变更说明、更新清单和 SHA-256 统一生成；后台发布时核对本地下载包哈希和大小。
- 官网增加玩法说明、五个榜单入口、真实空榜状态与下载校验信息；保留统计脚本，后台继续要求认证并禁止索引。

## 实测环境

Windows 11 x64，构建 26300；单显示器 2560×1080，工作区 2560×1032，DPI 96；桌面共 106 个图标。Python 3.12.14，pygame-ce 2.5.7，pystray 0.19.5。服务端部署目标使用 Python 3.11。

## 验收结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整自动回归 | 222 项通过，46.918 秒 | `build/optimization-regression-release2.log` |
| 官网示意图说明调整后复测 | 13 项官网测试通过，7.065 秒；打包脚本直接运行的模块路径问题已修正并实际生成归档 | `build/optimization-website-final-label-tests.log`、`build/optimization-package-final.log` |
| 物理鼠标击球及重开 | 连续 200 次通过；为轻击后重开，并非 200 局完整清台 | `build/optimization-acceptance/report.json` |
| F1 与任务栏恢复 | 连续 50 次通过 | 同上 |
| 前后台、音量、静音、改键 | 切到其他窗口隐藏；实际音频设备初始化；改键后实际按键生效；已占用组合提示冲突 | 同上及实机截图 |
| 四种模式 | 均能启动；90 秒模式接受实际鼠标击球、到时结算、保存本机成绩 | 同上 |
| 源程序退出 | 返回 0，布局记录 inactive | 同上 |
| 最终 EXE 启动退出 | 四种模式合计 20 次，均返回 0，桌面图标恢复可见；退出耗时见逐次报告 | `build/optimization-exe-acceptance/report.json` |
| 最终 EXE 完整挑战 | 实际鼠标击球后 90 秒结算，SQLite 记录 90000ms；结算卡正确显示进球率；退出返回 0，桌面恢复 | `build/optimization-exe-acceptance/round-report.json`、`build/optimization-exe-acceptance/packaged-sprint-result.png` |
| 无响应恢复 | 只挂起本次 EXE 自身 UI 进程，15.594 秒后守护终止并恢复桌面 | `build/optimization-exe-acceptance/report.json` |
| 网络响应丢失 | 本机真实 HTTP 服务已保存成绩后断开响应；客户端重启后待提交仍在，30.2 秒后重试成功，服务端最终仅一条成绩 | `build/optimization-outbox-http.json` |
| 官网浏览器 | Edge 桌面与 390px 移动视口；五榜切换、下载链接及哈希显示通过；无 JS 错误、无移动端横向溢出 | `build/optimization-website/report.json` |
| 已部署官网浏览器 | 直接访问 HTTPS 官网；五榜切换、全部图片加载、下载版本及哈希正确；390px 视口无横向溢出、无 JS 错误 | `build/optimization-website/live-report.json` |
| 公开发布与兼容性 | 远程完整下载的大小及 SHA-256 与最终测试包一致；保留 0.2.2，旧版更新解析代码识别出 0.3.0 | `build/optimization-online-after.json` |
| 源码检查 | compileall、JavaScript 语法检查、git diff --check 通过 | 对应执行记录 |

以上排名验收均使用本机临时数据库，没有向线上注入测试玩家或测试成绩。

## 排查记录及边界

早期诊断运行曾记录一次动画心跳超时恢复，以及一次 Python 原生访问冲突。后续发现高频 JSON 观测文件的重命名会被文件占用打断，导致观测值停更；观测改为共享内存后，完整实机流程通过。访问冲突出现于开启周期 faulthandler 的诊断会话，最终正式 EXE 与完整源程序复测未再次出现。该次原生异常尚未取得完整原生栈归因，不将其宣称为已经证明修复，仍需继续观察。

未执行 Windows 10、150%/200% 缩放、双屏、Explorer 重启、锁屏/休眠恢复及全新机器下载后首次启动的完整组合验收。独立守护恢复是明确的故障恢复机制，不代替卡死根因排查。

当前 EXE 的 Authenticode 状态为 **NotSigned**。没有可信签名证书；Microsoft 认证、SmartScreen 信誉验收和有效 Defender 扫描结果均未取得，不能以本记录承诺没有 Windows 安全提示。

## 产物

- 文件：`dist/DesktopIconBilliards-0.3.0-win-x64.exe`
- 大小：30,201,204 字节
- SHA-256：`1053cc13407e7c25567a04d2c0c9d87dbe7bfae30c279d98d24ec393530605fc`
- 源码编辑前快照：`build/optimization-backup-20261117-220548/`
- 本次沿用此前未提交的修改，未重置工作区或删除原有版本。

## 线上发布复核

0.3.0 于 2026-11-17 23:58:17（UTC+8）首次发布，2026-11-18 补充官网历史示意图说明并完成公开接口和浏览器复核；客户端 EXE 未再修改。

- 官网、后台与排名服务已同步更新，`deskpools.service` 为 active；源码仍位于真实目录 `/www/wwwroot/deskpools.com`，沿用既有 Nginx/TLS 与 systemd 服务。
- 公开下载文件为本页所列最终测试包，完整 HTTP 下载的大小与 SHA-256 一致；后台当前版本为 0.3.0，0.2.2 的记录与下载包保留。
- 原有数据库仍为 60 条电脑成绩、0 条真人成绩，电脑仅在「电脑挑战」中显示；标准、90 秒、每日和旧版真人榜为空。本次没有向生产环境提交测试成绩。
- 首页没有后台导航，统计脚本保留；robots.txt 排除后台，后台 HTML 和响应头均含 noindex，匿名访问管理 API 返回 401。这里只验证网站配置，不承诺搜索引擎的实际处理结果。
- 官网旧版成绩配图明确标注历史界面示意，图中的名次与人数不代表当前榜单；静态游戏截图也改标为 PREVIEW。
- 发布前已保存源码快照 `/www/wwwroot/.deskpools-source-backups/20261117-155815/previous`，数据库及配置备份位于 `/var/backups/deskpools/20261117-155815`。
- 官网说明调整前又保存源码 `/www/wwwroot/.deskpools-source-backups/20261117-160647/previous` 及数据配置 `/var/backups/deskpools/20261117-160647`；最终服务器归档 SHA-256 为 `7742e92dc74f0f42564fb5ffca8e21dbef43d263fdde9d704bb047cc765f2513`。
- 证据：`build/optimization-deploy.log`、`build/optimization-deploy-site-label.log`、`build/optimization-server-after.log`、`build/optimization-online-after.json`、`build/optimization-website/live-report.json`。测试结束后未遗留游戏客户端或本机测试后台进程，桌面恢复。
