#!/usr/bin/env python3
"""Build the pinned IT8613 driver for Debian 6.1.0-39, inside the builder stage."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

KERNEL = '6.1.0-39-amd64'
VERSION = 'nasdisplay-a904dd88'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=args.source, check=True)
    source = out / 'source'
    shutil.copytree(args.source, source)
    path = source / 'it87.c'
    text = path.read_text()
    old = '\tgigabyte_class = class_create("gigabyte");'
    assert text.count(old) == 1
    # class_create lost its module-owner argument in Linux 6.4. Keep the original
    # sensor/control code intact; only adapt this build's API call for 6.1.
    text = text.replace(old, '#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 4, 0)\n'
                        '\tgigabyte_class = class_create(THIS_MODULE, "gigabyte");\n'
                        '#else\n' + old + '\n#endif')
    path.write_text(text)
    subprocess.run(['make', f'TARGET={KERNEL}', f'DRIVER_VERSION={VERSION}', 'CC=gcc-12'], cwd=source, check=True)
    built = source / 'it87.ko'
    def info(path, field):
        return subprocess.check_output(['modinfo', '-F', field, str(path)], text=True).strip()
    assert info(built, 'version') == VERSION
    assert info(built, 'vermagic').startswith(KERNEL + ' ')
    assert info(built, 'depends') == 'hwmon-vid'
    dep = Path('/lib/modules') / KERNEL / 'kernel/drivers/hwmon/hwmon-vid.ko'
    assert dep.is_file()
    assert info(dep, 'vermagic').startswith(KERNEL + ' ')
    shutil.copyfile(built, out / 'it87.ko')
    shutil.copyfile(dep, out / 'hwmon-vid.ko')
    # Strip debug information only; preserve __versions / kernel ABI validation.
    subprocess.run(['strip', '--strip-debug', str(out/'it87.ko')], check=True)
    manifest = {
        'kernel': KERNEL, 'debian_version': '6.1.148-1', 'driver_version': VERSION,
        'source_commit': 'a904dd88b295a1bd4eeb47af523d8fad1e566a9f',
        'compatibility_change': 'class_create(THIS_MODULE, name) for kernels older than 6.4; sensor and fan logic unchanged',
        'debian_snapshot': 'https://snapshot.debian.org/archive/debian/20250908T000000Z/',
        'compiler': subprocess.check_output(['gcc-12', '-dumpfullversion'], text=True).strip(),
        'vermagic': info(out/'it87.ko', 'vermagic'), 'signed': False,
        'modules': {name: hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('hwmon-vid.ko', 'it87.ko')},
        'headers_config_sha256': hashlib.sha256((Path('/usr/src')/f'linux-headers-{KERNEL}/.config').read_bytes()).hexdigest(),
        'module_symvers_sha256': hashlib.sha256((Path('/usr/src')/f'linux-headers-{KERNEL}/Module.symvers').read_bytes()).hexdigest(),
    }
    (out/'MODULES.json').write_text(json.dumps(manifest, indent=2) + '\n')
    subprocess.run(['make', f'TARGET={KERNEL}', 'clean'], cwd=source, check=True)
    # The delivered source is patched, so its checksum manifest must match it.
    (source/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                                          for p in sorted(source.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
    print('PASS matched IT8613 module, dependency, vermagic, hashes and corresponding source')


if __name__ == '__main__': main()
