"""Package a verified .app using Apple's disk-image tools, then mount and test it."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from tempfile import TemporaryDirectory


def run(*arguments, **options):
    return subprocess.run(arguments, check=True, timeout=180, **options)


def package(app: Path, output: Path, arch: str, version: str):
    if sys.platform != 'darwin':
        raise RuntimeError('DMG packaging requires macOS disk-image services')
    app = app.resolve(strict=True)
    output = output.absolute()
    if app.suffix != '.app' or output.suffix != '.dmg':
        raise ValueError('Expected an existing .app and a .dmg output path')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info['CFBundleShortVersionString'] != version:
        raise ValueError('The application and installer versions must match')
    executable = info['CFBundleExecutable']
    if Path(executable).name != executable:
        raise ValueError('Unexpected bundle executable name')
    original_binary = app / 'Contents/MacOS' / executable
    with original_binary.open('rb') as stream:
        expected_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)

    # All transient files belong to this newly-created directory. The existing
    # installer is replaced only after creation, mounting and verification pass.
    with TemporaryDirectory(prefix='.deskpools-dmg-', dir=output.parent) as temp:
        stage = Path(temp)
        payload = stage / 'contents'
        payload.mkdir()
        run('ditto', str(app), str(payload / app.name))
        (payload / 'Applications').symlink_to('/Applications', target_is_directory=True)
        (payload / '安装说明.txt').write_text(
            '桌面图标台球 · 安装说明\n\n'
            '1. 把 Desktop Icon Billiards 拖入旁边的 Applications（应用程序）文件夹。\n'
            '2. 完成复制后弹出本安装盘。\n'
            '3. 从“应用程序”打开 Desktop Icon Billiards。\n'
            '4. 首次使用时允许读取桌面文件夹，以显示真实桌面图标。\n\n'
            '操作：按住鼠标左键蓄力，松手击球；右键取消；F1 或 fn-F1 暂停隐藏。\n'
            '菜单栏中的 🎱 可重开、调整球杆或退出。\n\n'
            f'版本：{version}，架构：{arch}。\n'
            '这是尚未完成 Developer ID 签名与 Apple 公证的测试包。\n'
            'Mac 联网排行榜、成绩卡片和更新检查尚未移植。\n\n'
            'Installation: drag the app onto Applications. Eject this disk, then\n'
            'launch Desktop Icon Billiards from Applications.\n',
            encoding='utf-8',
        )
        candidate = stage / 'installer.dmg'
        run('hdiutil', 'create', '-volname', 'DeskPools', '-fs', 'HFS+',
            '-format', 'UDZO', '-srcfolder', str(payload), '-o', str(candidate))
        run('hdiutil', 'verify', str(candidate))
        mount = stage / 'mounted'
        mount.mkdir()
        attached = run('hdiutil', 'attach', '-readonly', '-nobrowse', '-plist',
                       '-mountpoint', str(mount), str(candidate), capture_output=True)
        try:
            entities = plistlib.loads(attached.stdout)['system-entities']
            if not any(Path(entity.get('mount-point', '/')) == mount for entity in entities):
                raise RuntimeError('Disk image did not mount at the expected location')
            shortcut = mount / 'Applications'
            if not shortcut.is_symlink() or os.readlink(shortcut) != '/Applications':
                raise RuntimeError('Applications shortcut is missing or incorrect')
            if not (mount / '安装说明.txt').is_file():
                raise RuntimeError('Chinese installation instructions are missing')
            installed = mount / app.name
            installed_binary = installed / 'Contents/MacOS' / executable
            with installed_binary.open('rb') as stream:
                actual_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual_sha != expected_sha:
                raise RuntimeError('The mounted application executable differs from the build')
            run('lipo', str(installed_binary), '-verify_arch', arch)
            run('codesign', '--verify', '--deep', '--strict', str(installed))
            run(str(installed_binary), '--check-runtime',
                env={**os.environ, 'SDL_AUDIODRIVER': 'dummy'})
            print(f'DMG mount, Applications shortcut, signature and app runtime: PASS ({arch})', flush=True)
        finally:
            run('hdiutil', 'detach', str(mount))
        candidate.replace(output)

    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    output.with_suffix('.dmg.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(f'{digest}  {output}', flush=True)
    print(f'Built {output}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--arch', choices=('arm64', 'x86_64'), required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    package(args.app, args.output, args.arch, args.version)
