#!/usr/bin/env python3
"""Inspect and run the extracted FPK without installing it or using root."""
from verify_layout import check_links
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from release_config import HOST_VERSION, COMPONENTS as DIST
PACKAGE = DIST / f'NAS-Display-fnOS-{HOST_VERSION}-x86.fpk'


def extract(archive, dest):
    for member in archive.getmembers():
        assert not member.name.startswith('/') and '..' not in Path(member.name).parts
        assert member.isfile() or member.isdir(), member.name
        assert not member.mode & 0o002, member.name
    archive.extractall(dest, filter='data')


def main():
    with tempfile.TemporaryDirectory(prefix='nas-fpk-verify-') as temp:
        root = Path(temp)
        with tarfile.open(PACKAGE) as archive: extract(archive, root)
        manifest = dict(line.split('=', 1) for line in (root / 'manifest').read_text().splitlines() if '=' in line)
        manifest = {k.strip(): v.strip() for k, v in manifest.items()}
        assert manifest['checksum'] == hashlib.md5((root / 'app.tgz').read_bytes()).hexdigest()
        assert manifest['install_dep_apps'] == 'python312'
        assert manifest['service_port'] == '8787'
        assert manifest['maintainer'] == manifest['distributor'] == 'Dream'
        with tarfile.open(root / 'app.tgz') as archive: extract(archive, root / 'target')
        target = root / 'target'
        check_links(target.glob('*.md'), target)
        assert manifest['version'] == HOST_VERSION
        assert (target / 'server/fpk_fan.py').read_bytes() == (ROOT / 'src/host/fnos/fan_control.py').read_bytes()
        assert (target / 'server/fan_client.py').is_file()
        assert (target / 'server/fan_hwmon.py').is_file()
        kit = target / 'driver-kit'
        for name, digest in json.loads((kit / 'KIT-SHA256.json').read_text()).items():
            assert hashlib.sha256((kit / name).read_bytes()).hexdigest() == digest
        assert (kit / 'source/it87.c').read_bytes() == (ROOT / 'hardware/drivers/it87/source/it87.c').read_bytes()
        assert (kit / 'source/VERSION').read_text().strip() == 'nasdisplay-a904dd88'
        for path in target.rglob('*'):
            assert path.name not in ('config.json', 'auth.json', 'fan_service.py', 'FAN-PULSE.py')
            assert path.suffix != '.deb'
        for size, name in ((64, 'ICON.PNG'), (256, 'ICON_256.PNG')):
            png = (root / name).read_bytes()
            assert png[:8] == b'\x89PNG\r\n\x1a\n'
            assert struct.unpack('>II', png[16:24]) == (size, size)
            assert (target / f'ui/images/icon_{size}.png').read_bytes() == png
        for path in (root / 'cmd').iterdir():
            assert path.stat().st_mode & 0o111
            subprocess.run(['sh', '-n', str(path)], check=True)
        entry = json.loads((target / 'ui/config').read_text())['.url']['nas-display-fnos.main']
        assert entry['type'] == 'url' and entry['port'] == '8787'
        for name in ('install', 'config'):
            assert isinstance(json.loads((root / 'wizard' / name).read_text()), list)
            fields = json.loads((root / 'wizard' / name).read_text())[0]['items']
            assert any(item.get('field') == 'wizard_driver_policy' for item in fields)
        env = dict(os.environ, PYTHONPATH=str(target / 'vendor'), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
        subprocess.run(['/usr/bin/python3.12', '-s', str(target / 'server/cli.py'), '--version'], env=env, check=True)
        subprocess.run([str(target / 'bin/smartctl'), '--version'], check=True, stdout=subprocess.DEVNULL)
        # Import production lifecycle only to render units; never execute hooks.
        spec = importlib.util.spec_from_file_location('fpk', target / 'server/fpk_lifecycle.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.PYTHON = '/usr/bin/python3.12'  # Only syntax check on a non-fnOS host.
        units = []
        for name, content in module.unit_files(target, root / 'state').items():
            path = root / name; path.write_text(content); units.append(str(path))
        subprocess.run(['systemd-analyze', 'verify', *units], check=True)
        subprocess.run([sys.executable, str(ROOT / 'tools/verify_host_browser.py'), '--app-dir', str(target / 'server'),
                        '--server-python', '/usr/bin/python3.12', '--no-screenshots'], env=env, check=True)
    print('PASS FPK Dream metadata, pinned driver source, setup wizard, official container checksum, icons, payload allowlist, hooks syntax, unit rendering, bundled dependencies and extracted-package live web without a screen')
    print('NOT TESTED: fnOS app-center install/start/upgrade/uninstall or target hardware sensors')


if __name__ == '__main__': main()
