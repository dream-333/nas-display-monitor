#!/usr/bin/env python3
"""Send compact NAS samples over LAN UDP to the display. No dependencies."""
import argparse
import json
import math
from pathlib import Path
import socket
import time
import collect
from board import select_system


def metric(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def select_gpu(sample, config):
    devices = sample.get('gpu', [])
    selected = config.get('gpu_device', 'auto')
    if selected == '':  # Existing explicit "do not display" choice remains valid.
        return {}
    if selected == 'auto':
        return next((g for g in devices if g.get('boot_vga')), next(iter(devices), {}))
    return next((g for g in devices if g['device'] == selected), devices[0] if len(devices) == 1 else {})


def packet(sample, config):
    gpu = select_gpu(sample, config)
    net = sample['network'].get(config['interface'], {})
    models = config.get('disks', ([d['model'] for d in sample.get('nvme', [])] + ['', ''])[:2])
    disks = {d['model']: d for d in sample.get('nvme', [])}
    all_disks = sample.get('disks', [])
    def chosen(key):
        matches = [d for d in all_disks if d['id'] == key or d['model'] == key]
        if len(matches) == 1: return metric(matches[0].get('temperature_c'))
        if matches: return None  # ambiguous legacy model: require explicit identity
        return metric(disks.get(key, {}).get('composite_c'))

    data = {'v': 1, 'cpu': metric(sample['cpu_used_percent']),
            'ct': metric(collect.select_cpu_temperature(sample, config.get('cpu_sensor', 'auto'))['value_c']), 'mem': metric(sample['memory']['used_percent']),
            'gpu': metric(gpu.get('busy_percent')), 'gt': metric(gpu.get('temperature_c', gpu.get('edge_c'))),
            'd1': chosen(models[0]),
            'd2': chosen(models[1]),
            'rx': metric(net.get('rx_bytes_per_sec')), 'tx': metric(net.get('tx_bytes_per_sec'))}
    if 'disks' in sample or 'board_temperatures' in sample:
        start = (int(time.monotonic() // 10) % max(1, (len(all_disks) + 3) // 4)) * 4
        codes = {'ok': 'ok', 'sleeping': 'sleep', 'stale': 'old', 'pending': 'wait'}
        data.update(st=metric(select_system(sample, config.get('sys_sensor', 'auto')).get('temperature_c')),
                    dh=max((d['temperature_c'] for d in all_disks if metric(d.get('temperature_c')) is not None), default=None),
                    di=start, dn=len(all_disks),
                    ds=[{'n': (d['kind'] + ' ' + d['device'])[:16], 't': metric(d.get('temperature_c')),
                         's': codes.get(d['status'], 'n/a')} for d in all_disks[start:start + 4]])
        fans = sample.get('fans', [])
        data.update(f1=metric(fans[0].get('rpm')) if fans else None,
                    f2=metric(fans[1].get('rpm')) if len(fans) > 1 else None)
    return json.dumps(data, separators=(',', ':'), allow_nan=False).encode('ascii')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    p.add_argument('--init', action='store_true', help='Create private configuration without overwriting')
    p.add_argument('--count', type=int, default=0)
    args = p.parse_args()
    if args.init:
        cfg = {'display_ip': 'CHANGE_ME', 'port': 44445,
               'interface': next(iter(sorted(n for n in collect.network() if (Path('/sys/class/net') / n / 'device').exists())), next(iter(collect.network()), '')),
               'interval': 2, 'gpu_device': 'auto',
               'disks': ([d['model'] for d in collect.temperatures()['nvme']] + ['', ''])[:2]}
        import os
        fd = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(cfg, f, indent=2)
            f.write('\n')
        print('Created', args.config, '- edit display_ip to match the screen Wi-Fi address.')
        return
    cfg = json.loads(args.config.read_text())
    import ipaddress
    ipaddress.IPv4Address(cfg['display_ip'])
    interval = float(cfg.get('interval', 2))
    if not math.isfinite(interval) or not 0.2 <= interval <= 5 or args.count < 0:
        p.error('interval must be 0.2..5 seconds; count must be nonnegative')
    port = int(cfg.get('port', 44445))
    if not 1 <= port <= 65535:
        p.error('invalid port')
    if len(cfg.get('disks', [])) != 2:
        p.error('configure exactly two disk model names')
    previous = collect.snapshot()
    if cfg['interface'] not in previous[2]:
        p.error('interface not found: ' + cfg['interface'])
    i = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            while not args.count or i < args.count:
                time.sleep(interval)
                current = collect.snapshot()
                data = packet(collect.report(previous, current, [cfg['interface']]), cfg)
                previous = current
                try:
                    sock.sendto(data, (cfg['display_ip'], port))
                except OSError as e:
                    print('UDP send failed:', e, flush=True)
                i += 1
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
