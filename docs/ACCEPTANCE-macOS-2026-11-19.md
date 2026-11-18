# macOS 构建验收记录 · 2026-11-19

仓库：LeiLeiQAQ/deskpools
分支：codex/macos-build
提交：0c948469be9362d3ee6199a78d4f9aee55f9e353
Actions 运行：44382511409

## 已完成
- GitHub macos-15 / macos-15-intel 两个真实 macOS 运行器完成构建。
- lipo 架构检查、codesign 签名完整性验证、Info.plist 格式检查通过。
- 已打包程序通过原生文件图标读取、球台绘制、鼠标事件蓄力和击球、白球移动、音效加载及落袋音效启动检查。
- 两个 ZIP 已下载至 dist/macos，并分别核对 GitHub artifact 摘要和构建日志中的 SHA-256。
- 下载包 ZIP 完整性、主程序 Mach-O 架构、版本信息和 WAV 资源检查通过。

## 发布前尚待验证
- Finder 真实桌面图标层级、跨应用焦点切换、多显示器、F1 全局快捷键、暂停恢复及退出的交互验收。
- 当前包使用临时签名，未使用 Developer ID 签名，也未完成 Apple 公证；不能据此承诺首次打开没有 Gatekeeper 提示。
- Mac 联网排行榜、成绩卡片、更新检查尚未移植。

本记录不将自动自检等同于完整人工实机验收。Windows 版本及官网生产下载未变更。


## DMG 安装盘补充验收

提交：f141ed630a2631d27254acfc75b74ce3fc2f1203
Actions：44387357131；Apple 芯片与 Intel 均成功。
- 使用 hdiutil 原生生成 DMG；镜像校验通过。
- 云端只读挂载后确认应用、指向 /Applications 的快捷方式和中文安装说明。
- 挂载盘内的应用架构检查、签名完整性检查及原生图标/绘制/击球/音效自检通过。
- 两个 DMG 已下载到 dist/macos，校验文件、CI artifact 摘要、UDIF 文件尾均已核对。
- 安装：打开 DMG，把应用拖入 Applications；复制完成后弹出磁盘，从应用程序启动。
- Developer ID 签名及 Apple 公证仍未完成；DMG 容器不改变此状态。
