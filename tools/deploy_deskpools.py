"""Install a prepared DeskPools release on its existing BaoTa Nginx site.

Run on the server as root after uploading the release archive. Existing source,
Nginx configuration, database and administrative token are retained/backed up.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen, Request


def run(*command):
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(text, encoding='utf-8')
    temporary.chmod(mode)
    temporary.replace(path)


def install_source(release, webroot, stamp):
    """Publish real source directories in BaoTa's webroot and retain old trees."""
    if webroot.is_symlink() or not webroot.is_dir():
        raise ValueError('The BaoTa website root must be an existing real directory')
    history = webroot.parent / '.deskpools-source-backups'
    history.mkdir(mode=0o700, exist_ok=True)
    history.chmod(0o700)
    transaction = history / stamp
    incoming = transaction / 'incoming'
    previous = transaction / 'previous'
    incoming.mkdir(parents=True)
    previous.mkdir()
    names = [item.name for item in release.iterdir()]
    for name in names:
        source, target = release / name, incoming / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(source, target)
    activated, saved = [], []
    try:
        for name in names:
            target = webroot / name
            if target.exists() or target.is_symlink():
                target.replace(previous / name)
                saved.append(name)
            (incoming / name).replace(target)
            activated.append(name)
    except Exception:
        for name in reversed(activated):
            (webroot / name).replace(incoming / name)
        for name in reversed(saved):
            (previous / name).replace(webroot / name)
        raise
    return str(previous)


def deploy(archive):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    app = Path('/opt/deskpools')
    webroot = Path('/www/wwwroot/deskpools.com')
    data = Path('/var/lib/deskpools')
    backup = Path('/var/backups/deskpools') / stamp
    conf = Path('/www/server/panel/vhost/nginx/deskpools.com.conf')
    extension = Path('/www/server/panel/vhost/nginx/extension/deskpools.com/deskpools-backend.conf')
    service = Path('/etc/systemd/system/deskpools.service')
    environment = Path('/etc/deskpools.env')
    assert os.geteuid() == 0
    assert conf.is_file(), 'Existing BaoTa site configuration is required'
    assert Path('/www/server/panel/vhost/cert/deskpools.com/fullchain.pem').is_file()
    backup.mkdir(parents=True, mode=0o700)
    for path in (conf, extension, service, environment):
        if path.exists():
            shutil.copy2(path, backup / path.name)
    for name in ('site.db', 'rankings.db'):
        path = data / name
        if path.exists():
            with sqlite3.connect(path) as source, sqlite3.connect(backup / name) as target:
                source.backup(target)
    try:
        user = pwd.getpwnam('deskpools')
    except KeyError:
        run('useradd', '--system', '--home-dir', str(data), '--shell', '/usr/sbin/nologin', 'deskpools')
        user = pwd.getpwnam('deskpools')
    release = app / 'releases' / stamp
    release.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise ValueError('Release archive contains an invalid path')
        source.extractall(release)
    for folder in (data, data / 'downloads'):
        folder.mkdir(parents=True, exist_ok=True)
        os.chown(folder, user.pw_uid, user.pw_gid)
        folder.chmod(0o750)
    token = data / 'admin-token.txt'
    if not token.exists():
        write(token, secrets.token_urlsafe(32) + '\n', 0o600)
        os.chown(token, user.pw_uid, user.pw_gid)
    metadata = json.loads((release / 'update.json').read_text())
    package = release / 'dist' / Path(metadata['download_url']).name
    assert package.is_file(), 'Published executable is missing'
    destination = data / 'downloads' / package.name
    if destination.exists():
        shutil.copy2(destination, backup / package.name)
    # An in-progress public download must keep reading the complete old inode.
    pending_package = destination.with_name(destination.name + '.new')
    shutil.copy2(package, pending_package)
    pending_package.chmod(0o644)
    pending_package.replace(destination)
    if not (app / 'venv/bin/python').exists():
        run('python3', '-m', 'venv', '--without-pip', str(app / 'venv'))
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'deskpools.service']).returncode == 0
    if was_active:
        run('systemctl', 'stop', 'deskpools.service')
    try:
        source_backup = install_source(release, webroot, stamp)
    except Exception:
        if was_active:
            run('systemctl', 'start', 'deskpools.service')
        raise
    pending = app / 'current.new'
    pending.symlink_to(webroot, target_is_directory=True)
    pending.replace(app / 'current')
    write(environment, '''DIB_SITE_HOST=127.0.0.1
DIB_SITE_PORT=8070
DIB_SITE_DB=/var/lib/deskpools/site.db
DIB_RANK_DB=/var/lib/deskpools/rankings.db
DIB_SITE_DOWNLOAD_DIR=/var/lib/deskpools/downloads
DIB_TRUSTED_PROXIES=127.0.0.1/32,::1/128
''', 0o600)
    write(service, '''[Unit]
Description=DeskPools website, administration and ranking API
After=network.target

[Service]
Type=simple
User=deskpools
Group=deskpools
WorkingDirectory=/www/wwwroot/deskpools.com
EnvironmentFile=/etc/deskpools.env
ExecStart=/opt/deskpools/venv/bin/python -u -m website.server
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/deskpools

[Install]
WantedBy=multi-user.target
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'deskpools.service')
    run('systemctl', 'restart', 'deskpools.service')
    for attempt in range(50):
        try:
            with urlopen('http://127.0.0.1:8070/healthz', timeout=1) as response:
                health = json.load(response)
            if health.get('ok'):
                break
        except OSError:
            time.sleep(.2)
    else:
        raise RuntimeError('DeskPools backend failed its health check')
    request = Request('http://127.0.0.1:8070/api/admin/releases',
                      data=json.dumps(metadata).encode('utf-8'),
                      headers={'Authorization':'Bearer '+token.read_text().strip(),
                               'Content-Type':'application/json'},method='POST')
    with urlopen(request,timeout=20) as response:
        published = json.load(response)
    if published.get('version') != metadata['version']:
        raise RuntimeError('Published release version differs from the prepared archive')
    write(extension, '''# DeskPools unified service; preserve the site's existing TLS configuration.
client_max_body_size 256m;
location ^~ /.well-known/acme-challenge/ {
    root /www/wwwroot/deskpools.com;
}
location ^~ / {
    proxy_pass http://127.0.0.1:8070;
    proxy_http_version 1.1;
    proxy_cache off;
    proxy_cache_convert_head off;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 10s;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_request_buffering off;
}
''')
    try:
        print(run('nginx', '-t'))
    except subprocess.CalledProcessError:
        previous = backup / extension.name
        if previous.exists():
            shutil.copy2(previous, extension)
        else:
            extension.unlink()
        raise
    run('nginx', '-s', 'reload')
    print(json.dumps({'status': 'deployed', 'release': str(release), 'webroot': str(webroot),
        'source_backup': source_backup, 'backup': str(backup),
        'service': run('systemctl', 'is-active', 'deskpools.service').strip(),
        'backend': '127.0.0.1:8070', 'website': 'https://deskpools.com',
        'admin': 'https://deskpools.com/admin', 'health': health,
        'nginx_extension': str(extension)}, ensure_ascii=False))


if __name__ == '__main__':
    deploy(sys.argv[1])
