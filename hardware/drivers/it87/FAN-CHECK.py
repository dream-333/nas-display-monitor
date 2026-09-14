#!/usr/bin/env python3
"""Read-only fan recovery diagnostics; never writes PWM, modes or registers."""
import json
from pathlib import Path
import re
import time


ATTR = re.compile(r'(?:fan\d+_(?:input|label|fault|alarm|enable)|'
                  r'pwm\d+(?:_(?:enable|mode|freq|auto_channels_temp|'
                  r'auto_point\d+_(?:pwm|temp|temp_hyst)|auto_start|auto_slope))?|'
                  r'temp\d+_(?:input|label|type|fault|alarm))')


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def chips(root):
    result = []
    seen = set()
    for h in sorted(root.glob('hwmon*')):
        real = h.resolve()
        if real in seen:
            continue
        seen.add(real)
        name = read(h / 'name')
        if name is None:
            continue
        try:
            attrs = {p.name: read(p) for p in sorted(h.iterdir()) if ATTR.fullmatch(p.name)}
        except OSError:
            attrs = {'error': 'controller disappeared'}
        result.append({'driver': name, 'path': str(real), 'attributes': attrs})
    return result


def report(root=Path('/sys/class/hwmon'), system=Path('/sys'), proc=Path('/proc'), sleep=time.sleep):
    initial = chips(root)
    samples = []
    for _ in range(3):
        sleep(2)
        samples.append({'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                        'chips': [dict(c, attributes={k: v for k, v in c['attributes'].items()
                                  if re.fullmatch(r'fan\d+_input|pwm\d+(?:_enable)?|temp\d+_input', k)})
                                  for c in chips(root) if c['driver'] in ('it8613', 'coretemp')]})
    return {'read_only': True,
            'kernel': read(proc / 'sys/kernel/osrelease'),
            'driver_version': read(system / 'module/it87/version'),
            'board': {key: read(system / 'class/dmi/id' / key) for key in ('board_vendor', 'board_name')},
            'initial': initial, 'samples': samples}


if __name__ == '__main__':
    print(json.dumps(report(), ensure_ascii=False, indent=2))
