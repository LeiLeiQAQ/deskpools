"""Generate release notes and update metadata from the version and release content."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dib import __version__
from dib.release import NOTES, FEATURES

def generate(executable):
    previous = json.loads((ROOT/'update.json').read_text(encoding='utf-8'))
    history = previous.get('history', [])
    if previous.get('version') != __version__:
        history.insert(0, {'version': previous['version'], 'notes': previous.get('notes','')})
    metadata = dict(version=__version__, title=f'桌面图标台球 {__version__}',
                    download_url=f'https://deskpools.com/downloads/{executable.name}',
                    notes='\n'.join(NOTES), features=FEATURES, current=True,
                    sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),size=executable.stat().st_size,
                    history=[item for item in history if item['version'] != __version__])
    (ROOT/'update.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'update.example.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'RELEASE.md').write_text(f'# 桌面图标台球 v{__version__}\n\n'+
        '\n'.join('- '+line for line in NOTES)+
        '\n\nWindows 10 / 11 x64 · 单文件 EXE。退出旧版后运行新版即可。\n'+
        f'\n数字签名与 Microsoft 认证未完成；实机验收范围见 docs/ACCEPTANCE-{__version__}.md。\n',encoding='utf-8')
    return metadata

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',type=Path,required=True)
    print(json.dumps(generate(parser.parse_args().exe),ensure_ascii=True,indent=2))
