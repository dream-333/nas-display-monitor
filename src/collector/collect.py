#!/usr/bin/env python3
"""Read-only Linux NAS probe. Standard library only; outputs JSON lines."""
import argparse
import os
import hashlib
import csv
import re
import shutil
import subprocess
import json
import math
from pathlib import Path
import time
from storage import disk_stats
from board import board_stats
from intel_pmu import monitor as intel_monitor


PROC_ROOT = Path(os.environ.get('NAS_DISPLAY_PROC_ROOT', '/proc'))


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def number(path, divisor=1):
    try:
        value = float(read(path)) / divisor
        return round(value, 2) if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def fan_integer(path, maximum=None):
    raw = read(path)
    if raw is None or not re.fullmatch(r'[0-9]+', raw):
        return None
    value = int(raw)
    return value if maximum is None or value <= maximum else None


def fan_stats(hwmon_root=Path('/sys/class/hwmon')):
    """Discover tachometers and control attributes without writing to hardware.

    PWM and fan numbers do not establish a physical connection. Keep them
    separate until a board/driver-specific mapping has been verified.
    """
    fans, controls, chips = [], [], []
    seen = set()
    for h in sorted(hwmon_root.glob('hwmon*')):
        resolved = h.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        driver = read(h / 'name') or 'unknown'
        # Strip only the dynamically numbered hwmon class directory.
        parent = resolved.parent
        device = parent.parent if parent.name == 'hwmon' else parent
        base = {'driver': driver, 'device_path': str(device), 'hwmon': h.name}
        chips.append(dict(base))
        fan_channels = sorted({m[1] for p in h.glob('fan*')
                               if (m := re.fullmatch(r'(fan[1-9][0-9]*)_(?:input|target)', p.name))})
        for channel in fan_channels:
            path = h / (channel + '_input')
            value = fan_integer(path)
            enabled = fan_integer(h / (channel + '_enable'), 1)
            fault = fan_integer(h / (channel + '_fault'), 1)
            status = ('disabled' if enabled == 0 else 'fault' if fault == 1 else
                      'ok' if value is not None else 'unreadable' if path.exists() else 'not_exposed')
            fans.append(dict(base, channel=channel, label=read(h / (channel + '_label')) or channel,
                             rpm=value if status == 'ok' else None, status=status,
                             alarm=fan_integer(h / (channel + '_alarm'), 1),
                             target_rpm=fan_integer(h / (channel + '_target')),
                             target_exposed=(h / (channel + '_target')).exists()))
        for path in sorted(h.glob('pwm*')):
            if not re.fullmatch(r'pwm[1-9][0-9]*', path.name):
                continue
            value = fan_integer(path, 255)
            controls.append(dict(base, channel=path.name, raw=value,
                                 duty_percent=round(value * 100 / 255, 1) if value is not None and not (driver.startswith('it') and fan_integer(h / (path.name + '_enable')) == 2 and (h / (path.name + '_auto_start')).exists()) else None,
                                 enable=fan_integer(h / (path.name + '_enable')),
                                 mode=fan_integer(h / (path.name + '_mode'), 1),
                                 status='ok' if value is not None else 'unreadable'))
    return {'fans': fans, 'fan_controls': controls, 'fan_chips': chips}


def sensor_inputs(h):
    """All temperature channels, with their labels and actual read status."""
    result = []
    for path in sorted(h.glob('temp*_input')):
        value = number(path, 1000)
        if value is not None and not -50 <= value <= 150:
            value = None
        result.append({'label': read(path.with_name(path.name.replace('_input', '_label'))) or '',
                       'value_c': value, 'path': str(path), 'driver': read(h / 'name') or ''})
    return result


def temperatures(hwmon_root=Path('/sys/class/hwmon'), thermal_root=Path('/sys/class/thermal')):
    cpu_sources, tctl, disks, candidates = [], [], [], []
    for h in sorted(hwmon_root.glob('hwmon*')):
        name = read(h / 'name')
        channels = sensor_inputs(h)
        real = h.resolve()
        device = real.parent.parent if real.parent.name == 'hwmon' else real.parent
        for s in channels:
            channel = Path(s['path']).stem.removesuffix('_input')
            identity = f'{name}|{device}|{channel}'
            s['id'] = 'cpu-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
            if read(h / (channel + '_fault')) == '1' or read(h / (channel + '_enable')) == '0':
                s['value_c'] = None
        cpu_driver = name in ('coretemp', 'k10temp', 'zenpower', 'k8temp')
        if cpu_driver:
            candidates.extend(channels)
        elif name not in ('nvme', 'amdgpu', 'i915', 'xe', 'nouveau', 'nvidia'):
            candidates.extend(s for s in channels if s['label'].lower() in ('cpu', 'cpu package', 'cputin'))
        valid = [s for s in channels if s['value_c'] is not None]
        if name == 'coretemp':
            # A package per socket; on older CPUs use the hottest core if absent.
            packages = [s for s in valid if s['label'].lower().startswith(('package id', 'physical id'))]
            cores = [s for s in valid if s['label'].lower().startswith('core ')]
            cpu_sources.extend(packages or cores or [s for s in valid if not s['label']])
        elif name in ('k10temp', 'zenpower', 'k8temp'):
            tctl.extend(s['value_c'] for s in valid if s['label'].lower() == 'tctl')
            die = [s for s in valid if s['label'].lower() == 'tdie']
            control = [s for s in valid if s['label'].lower() == 'tctl']
            cpu_sources.extend(die or control or valid)
        elif name == 'nvme':
            device = next((p for p in h.resolve().parents if (p / 'model').is_file()), None)
            if device is None:
                continue
            composite = next((s for s in channels if s['label'].lower() == 'composite'), None)
            if composite is None:
                composite = next((s for s in channels if Path(s['path']).name == 'temp1_input' and not s['label']), None)
            disks.append({'device': device.name, 'model': read(device / 'model'),
                          'composite_c': composite['value_c'] if composite else None})
    if not cpu_sources:
        # Only explicitly CPU-labelled thermal zones; ACPI/board/SSD are not CPU.
        for zone in sorted(thermal_root.glob('thermal_zone*')):
            kind = (read(zone / 'type') or '').lower()
            if kind == 'x86_pkg_temp' or re.match(r'^cpu(?:[0-9]|[_-]|$)', kind):
                value = number(zone / 'temp', 1000)
                if value is not None and -50 <= value <= 150:
                    sensor = {'driver': 'thermal', 'label': kind, 'path': str(zone / 'temp'), 'value_c': value,
                              'id': 'cpu-' + hashlib.sha256(str(zone.resolve()).encode()).hexdigest()[:24]}
                    cpu_sources.append(sensor)
                    candidates.append(sensor)
    return {'cpu_temp_c': max((s['value_c'] for s in cpu_sources), default=None),
            'cpu_temperature_sources': cpu_sources,
            'cpu_temperature_candidates': candidates,
            # Kept for legacy readers; Intel/package readings never pretend to be Tctl.
            'cpu_tctl_c': max(tctl, default=None), 'nvme': disks}


def select_cpu_temperature(sample, selection='auto'):
    """One temperature selection shared by the web dashboard and wire packet.

    Auto preserves CPU-driver semantics (Intel package; AMD Tdie then Tctl).
    Explicit sensors never silently switch when missing or unreadable.
    """
    if selection == '':
        return {'value_c': None, 'status': 'disabled'}
    if selection != 'auto':
        matches = [s for s in sample.get('cpu_temperature_candidates', []) if s['id'] == selection]
        if len(matches) != 1:
            return {'value_c': None, 'status': 'missing'}
        sensor = matches[0]
        return dict(sensor, status='ok' if sensor['value_c'] is not None else 'unreadable', selection='manual')
    sources = [s for s in sample.get('cpu_temperature_sources', []) if s.get('value_c') is not None]
    if sources:
        return dict(max(sources, key=lambda s: s['value_c']), status='ok', selection='auto')
    value = sample.get('cpu_temp_c', sample.get('cpu_tctl_c'))
    return {'value_c': value, 'status': 'ok' if value is not None else 'unreadable', 'selection': 'auto'}


def pci_address(value):
    match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', value)
    if not match:
        return None
    domain, bus, device, function = (int(p, 16) for p in match.groups())
    return f'{domain:04x}:{bus:02x}:{device:02x}.{function:x}'


def numeric(value, low, high):
    try:
        result = float(value)
        return round(result, 2) if math.isfinite(result) and low <= result <= high else None
    except (TypeError, ValueError):
        return None


_nvidia_cache = (float('-inf'), [])


def nvidia_stats():
    """Optional proprietary driver backend. Never invokes a shell or needs sudo."""
    global _nvidia_cache
    now = time.monotonic()
    if now - _nvidia_cache[0] < 2:
        return _nvidia_cache[1]
    result = []
    tool = shutil.which('nvidia-smi')
    if tool:
        try:
            response = subprocess.run([tool, '--query-gpu=pci.bus_id,name,temperature.gpu,utilization.gpu',
                                       '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=1, check=True)
            for row in csv.reader(response.stdout.splitlines()):
                if len(row) != 4:
                    continue
                address = pci_address(row[0].strip())
                if address:
                    result.append({'device': address, 'name': row[1].strip(),
                                   'temperature_c': numeric(row[2].strip(), -50, 150),
                                   'busy_percent': numeric(row[3].strip(), 0, 100)})
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass
    _nvidia_cache = (now, result)
    return result


def gpu_stats(drm_root=Path('/sys/class/drm'), nvidia_reader=None, hwmon_root=Path('/sys/class/hwmon'), intel_reader=None):
    devices, seen = [], set()
    for card in sorted(drm_root.glob('card*')):
        if not card.name[4:].isdigit() or not (card / 'device').is_dir():
            continue
        device = (card / 'device').resolve()
        if device in seen:
            continue
        seen.add(device)
        driver = (device / 'driver').resolve().name if (device / 'driver').is_symlink() else ''
        vendor_id = read(device / 'vendor')
        vendor = {'0x8086': 'Intel', '0x1002': 'AMD', '0x10de': 'NVIDIA'}.get(vendor_id,
                 {'i915': 'Intel', 'xe': 'Intel', 'amdgpu': 'AMD', 'radeon': 'AMD', 'nouveau': 'NVIDIA', 'nvidia': 'NVIDIA'}.get(driver, 'GPU'))
        hwmons = {h.resolve() for h in (device / 'hwmon').glob('hwmon*')}
        hwmons.update(h.resolve() for h in hwmon_root.glob('hwmon*') if device in h.resolve().parents)
        channels = [s for h in sorted(hwmons) for s in sensor_inputs(h)]
        # Prefer edge/package over hotspot; never use a VRAM temperature as GPU core.
        eligible = [s for s in channels if not any(word in s['label'].lower() for word in ('mem', 'vram'))]
        def priority(s):
            label = s['label'].lower()
            return 0 if label in ('edge', 'gpu', 'package', 'gpu core') else 1 if not label else 2
        valid = sorted((s for s in eligible if s['value_c'] is not None), key=priority)
        temp = valid[0] if valid else None
        edge = next((s['value_c'] for s in channels if s['label'].lower() == 'edge'), None)
        devices.append({'card': card.name, 'device': pci_address(device.name) or device.name,
                        'driver': driver, 'vendor': vendor, 'name': vendor + ' / ' + (driver or card.name),
                        'boot_vga': read(device / 'boot_vga') == '1',
                        'temperature_c': temp['value_c'] if temp else None,
                        'temperature_source': (temp['driver'] + ' / ' + (temp['label'] or Path(temp['path']).stem)) if temp else None,
                        'temperature_status': 'ok' if temp else 'unreadable' if eligible else 'not_exposed',
                        'edge_c': edge, 'busy_percent': numeric(read(device / 'gpu_busy_percent'), 0, 100)})
    intel = intel_reader if intel_reader is not None else intel_monitor.sample
    intel_devices = [g for g in devices if g['driver'] == 'i915' and g['busy_percent'] is None]
    pmu = intel(intel_devices)
    for gpu in intel_devices:
        gpu.update(pmu.get(gpu['device'], {}))
    reader = nvidia_reader if nvidia_reader is not None else nvidia_stats
    for nv in reader():
        found = next((g for g in devices if g['device'] == nv['device']), None)
        if found is None:
            found = {'card': None, 'device': nv['device'], 'driver': 'nvidia', 'vendor': 'NVIDIA',
                     'boot_vga': False, 'edge_c': None, 'temperature_c': None,
                     'temperature_source': None, 'temperature_status': 'not_exposed', 'busy_percent': None}
            devices.append(found)
        found['name'] = nv['name']
        if nv['temperature_c'] is not None:
            found.update(temperature_c=nv['temperature_c'], temperature_source='nvidia-smi', temperature_status='ok')
        if nv['busy_percent'] is not None:
            found['busy_percent'] = nv['busy_percent']
    return devices


def cpu_counters():
    raw = read(PROC_ROOT / 'stat')
    if not raw:
        raise RuntimeError('Cannot read /proc/stat; run on Linux NAS host')
    values = list(map(int, raw.splitlines()[0].split()[1:9]))
    return sum(values), values[3] + values[4]


def memory():
    values = {}
    for line in (read(PROC_ROOT / 'meminfo') or '').splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.strip().split()[0]) * 1024
    total, available = values.get('MemTotal'), values.get('MemAvailable')
    used = total - available if total is not None and available is not None else None
    return {'total_bytes': total, 'available_bytes': available,
            'used_percent': round(100 * used / total, 2) if total and used is not None else None}


def network():
    result = {}
    for line in (read(PROC_ROOT / 'net/dev') or '').splitlines():
        if ':' not in line:
            continue
        name, raw = line.split(':', 1)
        name, fields = name.strip(), raw.split()
        if name != 'lo':
            result[name] = (int(fields[0]), int(fields[8]))
    return result


def snapshot():
    return time.monotonic(), cpu_counters(), network()


def report(previous, current, selected):
    elapsed = current[0] - previous[0]
    total, idle = current[1][0] - previous[1][0], current[1][1] - previous[1][1]
    interfaces = {}
    for name, counters in current[2].items():
        if selected and name not in selected:
            continue
        before = previous[2].get(name)
        rates = [None, None]
        if before is not None and elapsed > 0:
            rates = [round((new - old) / elapsed, 2) if new >= old else None for new, old in zip(counters, before)]
        interfaces[name] = {'rx_bytes_per_sec': rates[0], 'tx_bytes_per_sec': rates[1]}
    return {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'sample_seconds': round(elapsed, 3),
            'cpu_used_percent': round(100 * (total - idle) / total, 2) if total > 0 and 0 <= idle <= total else None,
            'disks': disk_stats(), 'board_temperatures': board_stats(), 'memory': memory(), **temperatures(), 'gpu': gpu_stats(), 'network': interfaces, **fan_stats()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--count', type=int, default=1, help='0 runs until Ctrl+C')
    parser.add_argument('--interface', action='append')
    parser.add_argument('--diagnose', action='store_true', help='List detected temperature sources and GPUs without sampling or sending')
    parser.add_argument('--diagnose-disks', action='store_true', help='Read physical disk inventory and cached temperatures; never wakes disks')
    parser.add_argument('--diagnose-fans', action='store_true', help='Read fan/PWM interfaces and board model; never changes fan settings')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0 or args.count < 0:
        parser.error('interval must be finite and positive; count must be nonnegative')
    if args.diagnose_disks:
        print(json.dumps({'disks': disk_stats(), 'board_temperatures': board_stats()}, ensure_ascii=False, indent=2))
        return
    if args.diagnose_fans:
        print(json.dumps({**fan_stats(), 'board': {name: read(Path('/sys/class/dmi/id') / name)
                         for name in ('board_vendor', 'board_name')}}, ensure_ascii=False, indent=2))
        return
    if args.diagnose:
        gpu_stats()  # PMU counters need two samples to calculate a rate.
        time.sleep(args.interval)
        print(json.dumps({**temperatures(), 'disks': disk_stats(), 'board_temperatures': board_stats(), 'gpu': gpu_stats(), **fan_stats()}, ensure_ascii=False, allow_nan=False, indent=2))
        return
    previous = snapshot()
    missing = set(args.interface or []) - set(previous[2])
    if missing:
        parser.error('Unknown interface: ' + ', '.join(sorted(missing)))
    i = 0
    try:
        while args.count == 0 or i < args.count:
            time.sleep(args.interval)
            current = snapshot()
            print(json.dumps(report(previous, current, args.interface), ensure_ascii=False, allow_nan=False), flush=True)
            previous = current
            i += 1
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
