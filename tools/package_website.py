"""Package the unified backend and the built client without local databases or secrets."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dib import __version__


def package():
    executable = ROOT / 'dist' / f'DesktopIconBilliards-{__version__}-win-x64.exe'
    if not executable.is_file():
        raise FileNotFoundError(executable)
    metadata = json.loads((ROOT/'update.json').read_text(encoding='utf-8'))
    if metadata['version'] != __version__ or metadata['sha256'] != hashlib.sha256(executable.read_bytes()).hexdigest():
        raise ValueError('Release metadata differs from the executable; run build.ps1 first')
    metadata['download_url'] = '/downloads/' + executable.name
    target = ROOT / 'build' / f'deskpools-{__version__}-server.tar.gz'
    files = [ROOT / 'LICENSE', executable, ROOT / 'assets/desktop-icon-billiards.png']
    for folder in ('website', 'ranking_server'):
        files.extend(path for path in (ROOT / folder).rglob('*')
                     if path.is_file() and 'data' not in path.relative_to(ROOT / folder).parts
                     and '__pycache__' not in path.parts and path.suffix in ('.py', '.html', '.css', '.js', '.md'))
    files.extend((ROOT / 'docs/images').glob('*'))
    with tarfile.open(target, 'w:gz') as bundle:
        for path in files:
            bundle.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
        payload = json.dumps(metadata, ensure_ascii=False, indent=2).encode('utf-8')
        item = tarfile.TarInfo('update.json')
        item.size = len(payload)
        item.mtime = int(datetime.now(timezone.utc).timestamp())
        item.mode = 0o644
        bundle.addfile(item, io.BytesIO(payload))
    print(json.dumps({'archive': str(target), 'version': __version__,
                      'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                      'archive_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    package()
