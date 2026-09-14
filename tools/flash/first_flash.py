#!/usr/bin/env python3
"""First install only. Verify files, require erase consent, then invoke esptool."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def verify(root):
    entries = json.loads((root/'FILES.json').read_text())
    seen = set()
    for entry in entries:
        rel = Path(entry['path'])
        if rel.is_absolute() or '..' in rel.parts or entry['path'] in seen:
            raise ValueError('Invalid file manifest')
        p = root/rel
        if not p.resolve().is_relative_to(root.resolve()) or p.is_symlink():
            raise ValueError('Invalid file path')
        seen.add(entry['path'])
        if hashlib.sha256(p.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Checksum failed: ' + entry['path'])
    if not {'factory.bin', 'FLASH.json', 'first_flash.py', 'first-flash.ps1', 'esptool.exe'} <= seen:
        raise ValueError('Incomplete first-flash package')
    info = json.loads((root/'FLASH.json').read_text())
    if (info['chip'], info['flash_size'], info['offset'], info['erase_all']) != ('esp32s3', 16777216, '0x0', True):
        raise ValueError('Unsupported target')
    if hashlib.sha256((root/'factory.bin').read_bytes()).hexdigest() != info['sha256']:
        raise ValueError('Factory image mismatch')


def commands(root, port):
    if not port or port.startswith('-') or any(c in port for c in '\r\n\x00'):
        raise ValueError('Invalid serial port')
    tool = [str(root/'esptool.exe')] if sys.platform == 'win32' else [sys.executable, '-m', 'esptool']
    base = tool + ['--chip', 'esp32s3', '--port', port, '--baud', '460800']
    return base + ['flash_id'], base + ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                '--flash_size', 'keep', '--erase-all', '0x0', str(root/'factory.bin')]


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description='First install: erases all device configuration. Exact AMOLED board only.')
    parser.add_argument('--port', required=True)
    parser.add_argument('--dry-run', action='store_true', help='Verify files and show commands without accessing hardware')
    parser.add_argument('--confirm-erase', action='store_true', help='Explicitly consent to erase ALL device flash')
    args = parser.parse_args(argv)
    root = root or Path(__file__).resolve().parent
    verify(root)
    probe, write = commands(root, args.port)
    if args.dry_run:
        print(json.dumps({'probe': probe, 'write': write}, ensure_ascii=False)); return 0
    print('LILYGO T-Display-S3 AMOLED non-touch / 16MB ONLY. ALL flash data and settings will be erased.')
    if not args.confirm_erase and input('Type ERASE to continue: ').strip() != 'ERASE':
        print('Cancelled; device not accessed.'); return 1
    result = subprocess.run(probe, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout)
    if not re.search(r'Detected flash size:\s*16\s*MB\b', result.stdout, re.I):
        raise ValueError('Expected 16MB flash; no erase/write performed')
    subprocess.run(write, check=True)
    print('SUCCESS. Release BOOT and press RESET. Configure USB sending in NAS Display.')
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, EOFError) as e:
        print('FAILED:', e, file=sys.stderr); sys.exit(1)
