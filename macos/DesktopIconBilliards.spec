import os
from pathlib import Path
import runpy

root = Path(SPECPATH).parent
version = runpy.run_path(str(root / 'dib' / '__init__.py'))['__version__']
identity = os.environ.get('DIB_MAC_SIGN_IDENTITY') or None
name = 'Desktop Icon Billiards'
assets = root / 'assets'
samples = ['impact-1.wav', 'impact-2.wav', 'impact-3.wav', 'pocket.wav']

a = Analysis(
    [str(root / 'macos' / 'main.py')],
    pathex=[str(root), str(root / 'macos')],
    datas=[(str(assets / sample), 'assets') for sample in samples] + [(str(root / 'LICENSE'), '.')],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='DesktopIconBilliards',
    console=False,
    upx=False,
    codesign_identity=identity,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
app = BUNDLE(
    coll,
    name=f'{name}.app',
    icon=str(assets / 'desktop-icon-billiards.png'),
    bundle_identifier='com.deskpools.desktop-icon-billiards',
    version=version,
    info_plist={
        'CFBundleDisplayName': '桌面图标台球',
        'CFBundleVersion': version,
        'LSUIElement': True,
        'NSHighResolutionCapable': True,
        'NSDesktopFolderUsageDescription': '选择桌面里的项目图标作为台球。游戏不会移动或修改桌面文件。',
    },
)
