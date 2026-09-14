"""Inspect and smoke-test the built deb without installing a system service."""
import argparse
from verify_layout import check_links
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from release_config import HOST_VERSION, COMPONENTS as DIST
PACKAGE = DIST / f'nas-display-host_{HOST_VERSION}_all.deb'


def main():
    data = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(PACKAGE)])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = archive.getmembers()
        assert all(m.uid == m.gid == 0 for m in members)
        assert not any(m.name.endswith(('config.json', 'auth.json', '.pyc')) or '.venv/' in m.name for m in members)
        assert not any(m.isfile() and m.mode & 0o002 for m in members)
        assert all(not m.name.startswith('/') and '..' not in Path(m.name).parts for m in members)
    with tempfile.TemporaryDirectory(prefix='nas-host-package-') as tmp:
        stage = Path(tmp)
        subprocess.run(['dpkg-deb', '-R', str(PACKAGE), str(stage / 'root')], check=True)
        root = stage / 'root'
        check_links((root/'usr/share/doc/nas-display-host').glob('*.md'), root)
        for line in (root / 'DEBIAN/md5sums').read_text().splitlines():
            expected, relative = line.split('  ', 1)
            assert hashlib.md5((root / relative).read_bytes()).hexdigest() == expected
        assert (root / 'usr/bin/nas-display-host').stat().st_mode & 0o111
        for name in ('postinst', 'prerm', 'postrm'):
            subprocess.run(['sh', '-n', str(root / 'DEBIAN' / name)], check=True)
        # Installed executable is not on this host. Use its extracted path solely
        # for unit syntax validation; nothing is started by systemd-analyze verify.
        unit = stage / 'nas-display-host.service'
        unit.write_text((root / 'lib/systemd/system/nas-display-host.service').read_text().replace('/usr/bin/nas-display-host serve', str(root / 'usr/bin/nas-display-host') + ' serve'))
        smart = stage / 'nas-display-smart.service'
        smart.write_text((root / 'lib/systemd/system/nas-display-smart.service').read_text().replace('/usr/lib/nas-display-host/smart_cache.py', str(root / 'usr/lib/nas-display-host/smart_cache.py')))
        timer = stage / 'nas-display-smart.timer'
        timer.write_text((root / 'lib/systemd/system/nas-display-smart.timer').read_text())
        assert 'smartmontools (>= 7.3)' in (root / 'DEBIAN/control').read_text()
        assert not (root / 'usr/lib/nas-display-host/fan_service.py').exists()
        fan = stage / 'nas-display-fan.service'
        fan.write_text((root / 'lib/systemd/system/nas-display-fan.service').read_text().replace('/usr/lib/nas-display-host/fpk_fan.py', str(root / 'usr/lib/nas-display-host/fpk_fan.py')))
        assert (root / 'usr/lib/nas-display-host/fan_hwmon.py').is_file()
        subprocess.run(['systemd-analyze', 'verify', str(unit), str(smart), str(timer), str(fan)], check=True)
        subprocess.run([sys.executable, str(ROOT / 'tools/verify_host_browser.py'), '--app-dir', str(root / 'usr/lib/nas-display-host'), '--no-screenshots'], check=True)
    print('PASS deb root ownership, explicit contents, private-file exclusion, file hashes, service syntax and extracted-package browser smoke test')


if __name__ == '__main__':
    main()
