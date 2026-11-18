# 桌面图标台球 macOS 版

Mac 版通过 AppKit 读取 Finder 桌面项目的原生图标，把图标绘制成圆形球。球台位于桌面层，进入其他应用时自动隐藏，程序不会移动或删除 Finder 文件。

- 按住左键蓄力，松手出杆。右键取消蓄力，白球落袋后点击有效位置重新摆球。
- 按 F1 暂停并隐藏球台，再按一次恢复。把 F1 用作亮度键的键盘可按 fn–F1，也可使用菜单栏的 🎱 控件。
- Esc 关闭球台，R 重开当前模式。菜单栏可切换自由练习、标准 15 球、90 秒挑战、每日挑战，以及三款球杆、台面显示和音效。
- 四种玩法暂为本机模式。联网排行榜、成绩卡片与更新检查还未移植。

## 构建与验证

## DMG 安装

根据 Mac 的芯片选择 arm64（Apple 芯片）或 x86_64（Intel）DMG。双击打开安装盘，把应用拖入旁边的 Applications 文件夹；复制完成后弹出安装盘，再从“应用程序”启动。

构建流程会同时生成 ZIP 和 DMG。DMG 中含应用、Applications 快捷方式、中文安装说明，并在上传前执行完整性校验、只读挂载、架构与签名检查及已挂载应用自检。测试包尚未完成 Developer ID 签名和 Apple 公证。

## 源码构建

在有 Python 3.13 和 Xcode 命令行工具的 Mac 上，从仓库根目录执行：

```sh
bash macos/build-macos.sh
open "dist/macos/Desktop Icon Billiards.app"
```

脚本会在 `dist/macos/` 生成当前机器架构的 `.app`。只有架构、签名检查和打包应用自检通过后，才会生成 ZIP。自检用临时文件走一遍原生图标读取、球台绘制、蓄力出杆、白球移动和落袋音效加载；不读取测试机的桌面文件，也不代替 Finder 桌面上的人工验收。设置 `DIB_MAC_SIGN_IDENTITY` 可使用已有 Developer ID 签名。外部发行还需 Apple 公证。

没有 Mac 时，可以在自己的 GitHub 仓库启用 Actions，并将源码推到 `codex/macos-build` 分支。**Build macOS app** 会在 Apple 芯片和 Intel 运行器上分别构建，成功后可在工作流的 Artifacts 中下载对应架构的 ZIP。不要把项目推到原作者的上游仓库。

在 Finder 桌面里放几个可见项目，并允许应用读取桌面文件夹。实机检查启动、图标清晰度、击球和落袋、F1 暂停及恢复、Esc 关闭、跨应用切换和再启动。本次开发在 Windows 工作区进行，尚未完成 Mac 构建和实机验收。
