"""Board temperature channels with stable identity and explicit SYS selection."""
import hashlib
from pathlib import Path
import re
from storage import read, celsius

EXCLUDED = {'coretemp','k10temp','k8temp','zenpower','nvme','drivetemp','acpitz','amdgpu','radeon','i915','xe','nouveau','nvidia'}
SYSTEM_LABELS = {'sys','system','system temperature','systin','motherboard','mb','board'}


def board_stats(root=Path('/sys/class/hwmon')):
    result = []
    for h in sorted(root.glob('hwmon*')):
        driver = read(h / 'name') or 'unknown'
        if driver in EXCLUDED: continue
        real = h.resolve()
        device = real.parent.parent if real.parent.name == 'hwmon' else real.parent
        for p in sorted(h.glob('temp*_input')):
            channel = p.name.removesuffix('_input')
            if not re.fullmatch(r'temp[0-9]+', channel): continue
            label = read(h / (channel + '_label')) or channel
            try: value = celsius(float(read(p)) / 1000)
            except (ValueError, TypeError): value = None
            fault = read(h / (channel + '_fault')) == '1'
            disabled = read(h / (channel + '_enable')) == '0'
            status = 'fault' if fault else 'disabled' if disabled else 'ok' if value is not None else 'invalid'
            identity = driver + '|' + str(device) + '|' + channel
            result.append({'id': 'board-' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                           'driver': driver, 'channel': channel, 'label': label,
                           'temperature_c': value if status == 'ok' else None,
                           'status': status, 'alarm': read(h / (channel + '_alarm')) == '1',
                           'system_label': label.lower() in SYSTEM_LABELS})
    return result


def select_system(sample, selection='auto'):
    sensors = sample.get('board_temperatures', [])
    if selection == '': return {'temperature_c': None, 'status': 'disabled'}
    if selection == 'auto':
        matches = [s for s in sensors if s['system_label']]
        if len(matches) != 1: return {'temperature_c': None, 'status': 'select_source'}
        return dict(matches[0], selection='auto')
    sensor = next((s for s in sensors if s['id'] == selection), None)
    return dict(sensor, selection='manual') if sensor else {'temperature_c': None, 'status': 'missing'}
