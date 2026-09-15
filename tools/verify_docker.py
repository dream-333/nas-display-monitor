#!/usr/bin/env python3
"""Run an isolated container: first login, real sampling/UDP, restart and health."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default='docker', choices=['docker', 'podman'])
    parser.add_argument('--image', default='nas-display:1.5.2')
    parser.add_argument('--configured-password', action='store_true', help='Verify GUI-style initial password environment instead of reading a generated password')
    args = parser.parse_args()
    initial_password = secrets.token_urlsafe(18) if args.configured_password else None
    name = 'nas-display-test-' + secrets.token_hex(4)
    volume = name + '-data'
    fan_volume, socket_volume, cache_volume = [name + s for s in ('-fan', '-socket', '-cache')]
    driver_volume = name + '-driver'
    fixture = tempfile.TemporaryDirectory(prefix='nas-display-fake-sys-')
    Path(fixture.name).chmod(0o755)
    (Path(fixture.name) / 'fs/cgroup').mkdir(parents=True)
    def run(*cmd):
        result = subprocess.run([args.engine, *cmd], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f'{args.engine} {cmd[0]} failed: {result.stderr.strip()}')
        return result.stdout.strip()
    with socket.socket() as pick:
        pick.bind(('127.0.0.1', 0)); port = pick.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def api(path, body=None, csrf=None):
        req = urllib.request.Request(base + '/api/' + path,
              data=None if body is None else json.dumps(body).encode(),
              headers={'Content-Type': 'application/json', **({'X-CSRF-Token': csrf} if csrf else {})})
        with browser.open(req, timeout=10) as reply: return json.load(reply)
    def ready():
        for _ in range(60):
            try: return api('session')
            except OSError: time.sleep(.5)
        raise RuntimeError('Container did not become ready')
    try:
        # Never grant SYS_MODULE in development verification; use empty simulated
        # sysfs so this test cannot load a driver even on a matching host kernel.
        run('run', '-d', '--name', name + '-driver', '--network=none', '--read-only',
            '--cap-drop=ALL', '--user=0:0', '--entrypoint=python',
            '-v', driver_volume + ':/run/nas-display-driver', '-v', fixture.name + ':/sys:ro',
            args.image, '/app/docker/driver_init.py')
        run('run', '-d', '--name', name + '-fans', '--network=none', '--read-only',
            '--cap-drop=ALL', '--cap-add=CHOWN', '--user=0:0', '--entrypoint=python',
            '-e', 'NAS_DISPLAY_FAN_ACCOUNT=nas-display',
            '-e', 'NAS_DISPLAY_FAN_STATE=/fan-state/fans/active.json',
            '-e', 'NAS_DISPLAY_FAN_SOCKET=/run/nas-display-fan/control.sock',
            '-v', fan_volume + ':/fan-state', '-v', socket_volume + ':/run/nas-display-fan',
            '-v', fixture.name + ':/sys:rw', args.image, '/app/fpk_fan.py')
        run('run', '-d', '--name', name + '-smart', '--network=none', '--read-only',
            '--cap-drop=ALL', '--user=0:0', '--entrypoint=python',
            '-v', cache_volume + ':/run/nas-display-smart', '-v', fixture.name + ':/sys:ro',
            args.image, '/app/docker/smart_loop.py')
        run('run', '-d', '--name', name, '--network=host', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--tmpfs=/tmp:rw,size=16m,mode=1777',
            '-e', 'NAS_DISPLAY_HARDWARE_STATUS=/run/nas-display-driver/status.json',
            '-v', driver_volume + ':/run/nas-display-driver:ro',
            *(['-e', 'NAS_DISPLAY_ADMIN_PASSWORD=' + initial_password] if initial_password else []),
            '-e', f'NAS_DISPLAY_PORT={port}', '-e', 'NAS_DISPLAY_PROC_ROOT=/host/proc',
            '-e', 'NAS_DISPLAY_FAN_SOCKET=/run/nas-display-fan/control.sock',
            '-v', socket_volume + ':/run/nas-display-fan:ro',
            '-v', cache_volume + ':/run/nas-display-smart:ro',
            '-v', volume + ':/data', '-v', '/sys:/sys:ro', '-v', '/proc:/host/proc:ro', args.image)
        session = ready()
        assert not session['authenticated']
        password = initial_password or run('exec', name, 'cat', '/data/initial-password.txt')
        if initial_password:
            run('exec', name, 'python', '-c', "from pathlib import Path; assert not Path('/data/initial-password.txt').exists()")
        session = api('login', {'password': password}, session['csrf'])
        fans = api('fans')
        assert isinstance(fans, dict) and fans.get('channels') == [] and not fans.get('error'), fans
        cached = run('exec', name, 'cat', '/run/nas-display-smart/disks.json')
        assert json.loads(cached) == {'disks': []}
        cfg = api('config')
        assert 'token' not in cfg
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', 0)); receiver.settimeout(15)
            cfg.update(transport='udp', display_ip='127.0.0.1', port=receiver.getsockname()[1],
                       enabled=True, interval=.5)
            api('config', cfg, session['csrf'])
            payload, _ = receiver.recvfrom(2048)
            packet = json.loads(payload)
            assert packet['v'] == 1 and 'token' not in packet and 0 <= packet['cpu'] <= 100 and 0 <= packet['mem'] <= 100
        cfg['enabled'] = False
        api('config', cfg, session['csrf'])
        status = api('status')
        assert status['version'] == '1.5.2'
        assert status['hardware_setup']['state'] in ('unsupported', 'needs_attention')
        assert status['hardware_setup']['message']
        manifest = json.loads(run('exec', name, 'cat', '/opt/it8613/MODULES.json'))
        assert manifest['kernel'] == '6.1.0-39-amd64'
        run('exec', name, 'python', '-c', "import hashlib,json; from pathlib import Path; p=Path('/opt/it8613'); m=json.loads((p/'MODULES.json').read_text()); assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in m['modules'].items()); assert (p/'source/it87.c').is_file(); assert (p/'linux-source-6.1.tar.xz').is_file()")
        before = run('exec', name, 'sha256sum', '/data/auth.json', '/data/config.json')
        run('restart', '--time', '30', name)
        ready()
        assert before == run('exec', name, 'sha256sum', '/data/auth.json', '/data/config.json')
        assert api('config') == cfg
        run('exec', name, 'python', '/app/docker/healthcheck.py')
        # Container image must not ship source checkout credentials or device backup.
        assert run('exec', name, 'python', '-c',
                   "from pathlib import Path; assert not Path('/app/config.json').exists(); assert not Path('/app/auth.json').exists(); print('clean')") == 'clean'
        print('PASS container: initial login, host sampling, token-free real UDP, persisted auth/config, restart, healthcheck and private-file exclusion; fan socket and SMART cache on simulated hardware; matched driver payload and visible load-refusal report')
    finally:
        for container in (name, name + '-fans', name + '-smart', name + '-driver'):
            subprocess.run([args.engine, 'rm', '-f', container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for v in (volume, fan_volume, socket_volume, cache_volume, driver_volume):
            subprocess.run([args.engine, 'volume', 'rm', v], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture.cleanup()


if __name__ == '__main__': main()
