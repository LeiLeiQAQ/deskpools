#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  printf '%s\n' 'Run this build on macOS or use the macOS GitHub Actions workflow.' >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
python="${DIB_MAC_PYTHON:-python3.13}"
work="$root/build/macos"
dist="$root/dist/macos"
app="$dist/Desktop Icon Billiards.app"

"$python" -m venv "$work/venv"
venv_python="$work/venv/bin/python"
"$venv_python" -m pip install --only-binary=:all: -r "$root/macos/requirements.txt"
"$venv_python" -m PyInstaller --noconfirm --workpath "$work/pyinstaller" --distpath "$dist" \
  "$root/macos/DesktopIconBilliards.spec"

binary="$app/Contents/MacOS/DesktopIconBilliards"
arch="$("$venv_python" -c 'import platform; print(platform.machine())')"
version="$("$venv_python" -c 'from dib import __version__; print(__version__)')"
lipo -verify_arch "$arch" "$binary"
codesign --verify --deep --strict "$app"
plutil -lint "$app/Contents/Info.plist"
SDL_AUDIODRIVER=dummy "$binary" --check-runtime

archive="$dist/DesktopIconBilliards-$version-macos-$arch.zip"
stage="$(mktemp -d "$dist/.package-XXXXXXXX")"
trap 'rm -f "$stage/app.zip"; rmdir "$stage"' EXIT
ditto -c -k --sequesterRsrc --keepParent "$app" "$stage/app.zip"
mv -f "$stage/app.zip" "$archive"
shasum -a 256 "$archive"
printf 'Built %s\n' "$archive"
