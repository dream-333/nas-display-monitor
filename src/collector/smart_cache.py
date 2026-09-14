#!/usr/bin/env python3
"""Root-only, fixed-command SMART collector. No socket, user commands or writes to disks."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from storage import CACHE, BLOCK, celsius, inventory, native_temperature


def parse_smart(data, code, power_checked=True):
    # Explicit -n return statuses: never confuse standby with disk health bits.
    if power_checked and code == 3: return None, 'sleeping'
    if power_checked and code == 5: return None, 'power_unknown'
    if not isinstance(data, dict): return None, 'unavailable'
    temp = data.get('temperature', {}).get('current')
    if temp is None: temp = data.get('nvme_smart_health_information_log', {}).get('temperature')
    value = celsius(temp)
    if code & 3: return None, 'unavailable'
    return (value, 'ok') if value is not None else (None, 'unsupported')


def query(disk, tool, runner=subprocess.run):
    kind = {'NVMe': 'nvme', 'SATA': 'ata', 'USB': 'sat', 'SCSI': 'scsi'}[disk['kind']]
    cmd = [tool, '-j', '-A', '-d', kind]
    if kind != 'nvme': cmd += ['-n', 'standby,3,5']
    cmd += ['/dev/' + disk['device']]
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=8, check=False)
        data = json.loads(r.stdout)
        return parse_smart(data, r.returncode, power_checked=kind != 'nvme')
    except subprocess.TimeoutExpired: return None, 'timeout'
    except (OSError, ValueError, TypeError, AttributeError): return None, 'unavailable'


def publish(disks, path=CACHE):
    path.parent.mkdir(mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.disks-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump({'disks': disks}, f, allow_nan=False); f.flush(); os.fsync(f.fileno())
            os.fchmod(f.fileno(), 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main():
    if os.geteuid() != 0: raise SystemExit('Run via nas-display-smart.service (root).')
    # A root-owned FPK may bundle smartctl. This is process configuration,
    # never a web setting or a device path supplied by a client.
    candidates = (os.environ.get('NAS_DISPLAY_SMARTCTL', ''), '/usr/sbin/smartctl', '/usr/bin/smartctl')
    tool = next((p for p in candidates if p and Path(p).is_file()), None)
    disks = inventory()
    try:
        previous = json.loads(CACHE.read_text()).get('disks', [])
        result = {d['id']: d for d in previous if d['id'] in {v['id'] for v in disks}}
    except (OSError, ValueError, KeyError, TypeError): result = {}
    for disk in disks:
        temp, source = native_temperature(disk, Path('/sys/class/hwmon'))
        if temp is not None:
            result.pop(disk['id'], None)
            continue  # NVMe/drivetemp direct sysfs is sufficient.
        dev = Path('/dev') / disk['device']
        try:
            info = dev.lstat()
            # Inventory only, no partitions, no user-supplied or symlink device paths.
            if not BLOCK.fullmatch(dev.name) or not stat.S_ISBLK(info.st_mode): continue
            expected = Path('/sys/dev/block') / f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
            if expected.resolve() != Path(disk['device_path']): continue
            temp, status = query(disk, tool) if tool else (None, 'tool_missing')
        except OSError: temp, status = None, 'unavailable'
        # No serial numbers, smartctl stdout/stderr or identification dump in cache.
        result[disk['id']] = {'id': disk['id'], 'temperature_c': temp, 'status': status, 'updated_at': time.time()}
        publish(list(result.values()))
    publish(list(result.values()))


if __name__ == '__main__': main()
