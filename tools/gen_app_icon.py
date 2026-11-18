"""Generate the original emerald 8-ball-and-cue PNG and Windows ICO frames."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dib.art import billiard_icon_art


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'assets')
    parser.add_argument('--master-size', type=int, default=1024)
    args = parser.parse_args()
    if args.master_size < 256:
        parser.error('--master-size must be at least 256')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    icon = billiard_icon_art(args.master_size)
    sizes = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
    frames = [billiard_icon_art(size) for size in sizes]
    for suffix, format_name in (('.png', 'PNG'), ('.ico', 'ICO')):
        path = args.output_dir / f'desktop-icon-billiards{suffix}'
        temporary = path.with_name(path.name + '.tmp')
        options = {'sizes': [(s, s) for s in sizes], 'append_images': frames,
                   'bitmap_format': 'bmp'} if suffix == '.ico' else {}
        icon.save(temporary, format=format_name, **options)
        temporary.replace(path)
        print(path)


if __name__ == '__main__':
    main()
