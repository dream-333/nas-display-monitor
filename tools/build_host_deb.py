#!/usr/bin/env python3
"""Build a small local Debian package from an explicit source allowlist."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
from release_config import HOST_VERSION as VERSION, COMPONENTS as DIST, USER_DOCS, release_metadata
OUT = DIST / f'nas-display-host_{VERSION}_all.deb'


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-host-deb-') as tmp:
        stage = Path(tmp) / 'package'
        def put(source, target, mode=0o644):
            dest = stage / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, dest)
            dest.chmod(mode)
        for name in ('app.py', 'cli.py', 'core.py', 'fan_curve.py', 'fan_hwmon.py', 'fan_client.py', 'hardware_status.py', 'usb_link.py', 'templates/index.html',
                     'static/app.js', 'static/fan-curves.js', 'static/app.css', 'static/icon.svg'):
            put('src/host/' + name, 'usr/lib/nas-display-host/' + name)
        for name in ('collect.py', 'send.py', 'intel_pmu.py', 'storage.py', 'board.py', 'smart_cache.py'):
            put('src/collector/' + name, 'usr/lib/nas-display-host/' + name)
        put('src/host/debian/nas-display-host.service', 'lib/systemd/system/nas-display-host.service')
        put('src/host/fnos/fan_control.py', 'usr/lib/nas-display-host/fpk_fan.py')
        for unit in ('nas-display-smart.service', 'nas-display-smart.timer', 'nas-display-fan.service'):
            put('src/host/debian/' + unit, 'lib/systemd/system/' + unit)
        put('src/host/debian/default', 'etc/default/nas-display-host')
        for name in USER_DOCS:
            put('docs/' + name, 'usr/share/doc/nas-display-host/' + name)
        put('docs/INSTALL.zh-CN.md', 'usr/share/doc/nas-display-host/README.zh-CN.md')
        (stage / 'usr/share/doc/nas-display-host/RELEASE.json').write_text(json.dumps(release_metadata(), indent=2) + '\n')
        for name in ('postinst', 'prerm', 'postrm'):
            put('src/host/debian/' + name, 'DEBIAN/' + name, 0o755)
        cli = stage / 'usr/bin/nas-display-host'
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/lib/nas-display-host/cli.py "$@"\n')
        cli.chmod(0o755)
        (stage / 'DEBIAN/conffiles').write_text('/etc/default/nas-display-host\n')
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024 + 1
        (stage / 'DEBIAN/control').write_text(f'''Package: nas-display-host
Version: {VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Dream <root@localhost>
Depends: smartmontools (>= 7.3), python3 (>= 3.9), python3-flask (>= 2.2), python3-waitress (>= 2.1), python3-serial (>= 3.5), adduser, init-system-helpers, systemd
Installed-Size: {size}
Description: NAS sensor sender and authenticated web management
 Collect CPU, GPU, memory, storage temperature, fan and network statistics.
 Control supported hwmon fan modes and PWM with a local privileged broker.
 Manage an AMOLED display over native USB or LAN UDP from a browser.
 Requires local password initialization; no default credentials.
''')
        files = [p for p in stage.rglob('*') if p.is_file() and 'DEBIAN' not in p.relative_to(stage).parts]
        (stage / 'DEBIAN/md5sums').write_text(''.join(hashlib.md5(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(stage)) + '\n' for p in sorted(files)))
        # TemporaryDirectory is 0700; package contents themselves must be traversable.
        for path in [stage, *(p for p in stage.rglob('*') if p.is_dir())]:
            path.chmod(0o755)
        OUT.parent.mkdir(exist_ok=True)
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(stage), str(OUT)], check=True)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    Path(str(OUT) + '.sha256').write_text(digest + '  ' + OUT.name + '\n')
    print(OUT)


if __name__ == '__main__':
    main()
