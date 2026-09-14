"""Physical block inventory and read-only cached SMART temperatures."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

CACHE = Path(os.environ.get('NAS_DISPLAY_SMART_CACHE', '/run/nas-display-smart/disks.json'))
BLOCK = re.compile(r'(?:sd[a-z]+|hd[a-z]+|nvme[0-9]+n[0-9]+)')


def read(path):
    try: return Path(path).read_text().strip()
    except OSError: return None


def public_read(path):
    try:
        return read(path) if Path(path).stat().st_mode & 0o004 else None
    except OSError: return None


def celsius(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 125:
        return None
    return round(value, 1)


def inventory(root=Path('/sys/class/block')):
    disks = []
    for b in sorted(root.iterdir()) if root.is_dir() else []:
        if not BLOCK.fullmatch(b.name) or (b / 'partition').exists(): continue
        real = b.resolve()
        model = read(b / 'device/model')
        serial = public_read(b / 'device/serial')
        controller = next((p for p in real.parents if (p / 'model').is_file()), None)
        if not model and controller: model = read(controller / 'model')
        if not serial and controller: serial = public_read(controller / 'serial')
        wwid = public_read(b / 'wwid') or public_read(b / 'device/wwid')
        # Same model disks must stay distinct. Identity isn't based on sdX numbering.
        identity = (wwid or serial or str(real)) + '|' + (model or '')
        if b.name.startswith('nvme'): identity += '|' + (read(b / 'nsid') or b.name.split('n')[-1])
        kind = 'NVMe' if b.name.startswith('nvme') else 'USB' if '/usb' in str(real) else 'SATA' if (read(b / 'device/vendor') or '').strip() == 'ATA' or '/ata' in str(real) else 'SCSI'
        disks.append({'id': 'disk-' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                      'device': b.name, 'model': model or b.name, 'kind': kind,
                      'device_path': str(real), 'size_bytes': int(read(b / 'size') or '0') * 512})
    return disks


def native_temperature(disk, hwmon_root):
    block = Path(disk['device_path'])
    for h in sorted(hwmon_root.glob('hwmon*')):
        name = read(h / 'name')
        if name not in ('nvme', 'drivetemp'): continue
        real = h.resolve()
        device = real.parent.parent if real.parent.name == 'hwmon' else real.parent
        if device not in block.parents: continue
        paths = sorted(h.glob('temp*_input'))
        for p in paths:
            label = (read(p.with_name(p.name.replace('_input', '_label'))) or '').lower()
            if name == 'nvme' and not (label == 'composite' or p.name == 'temp1_input' and not label): continue
            try: value = celsius(float(read(p)) / 1000)
            except (ValueError, TypeError): value = None
            if value is not None: return value, name
    return None, None


def disk_stats(root=Path('/sys/class/block'), hwmon_root=Path('/sys/class/hwmon'), cache=CACHE, now=None):
    now = time.time() if now is None else now
    try:
        cached = json.loads(cache.read_text())
        entries = {d['id']: d for d in cached.get('disks', [])}
    except (OSError, ValueError, KeyError, TypeError): entries = {}
    disks = inventory(root)
    for d in disks:
        value, source = native_temperature(d, hwmon_root)
        d.update(temperature_c=value, source=source, status='ok' if value is not None else 'pending', age_seconds=0 if value is not None else None)
        if value is not None: continue
        entry = entries.get(d['id'], {})
        stamp = entry.get('updated_at')
        age = now - stamp if type(stamp) in (float, int) and math.isfinite(stamp) else None
        if age is None: continue
        d['age_seconds'] = round(max(0, age))
        if not 0 <= age <= 180:
            d['status'] = 'stale'; continue
        d.update(temperature_c=celsius(entry.get('temperature_c')), source='smartctl', status=entry.get('status', 'unavailable'))
        if d['status'] != 'ok': d['temperature_c'] = None
        elif d['temperature_c'] is None: d['status'] = 'unavailable'
    return disks
