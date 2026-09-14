"""DISABLED DEVELOPMENT DRAFT: Zero1 pro automatic recovery failed in hardware.

Not shipped in the Debian package; hardware writes and startup are blocked.
The following lease/auto design is unvalidated and must not be enabled.

Local privileged fan broker. No TCP listener; only a root-approved profile.

Manual control is a 15-second renewable lease. A lost browser/web server,
temperature fault, stalled fan or service shutdown restores firmware auto mode.
The only implemented write ABI is the pinned IT8613 driver reviewed in this repo.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import stat
import struct
import tempfile
import time

import sys
if (Path(__file__).resolve().parents[2] / 'src/collector').is_dir():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/collector'))
import collect

PROFILE = Path('/etc/nas-display-fan.json')
STATE = Path('/var/lib/nas-display-fan/active.json')
SOCKET = '/run/nas-display-fan/control.sock'
DRIVER_VERSION = 'nasdisplay-a904dd88'
LEASE_SECONDS = 15


def require_verified_recovery():
    raise ValueError('调速未启用：Zero1 pro 的 IT8613 自动恢复测试出现 0 RPM，需先解决恢复异常。')


def save_json(path, value):
    fd, tmp = tempfile.mkstemp(prefix='.fan-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, allow_nan=False)
            output.flush(); os.fsync(output.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def integer(path):
    value = collect.read(path)
    if value is None or not re.fullmatch(r'[0-9]+', value):
        raise ValueError('风扇控制器读数不可用。')
    return int(value)


def validate_profile(p):
    keys = {'driver', 'device_path', 'board_vendor', 'board_name', 'module_version', 'mapping'}
    if not isinstance(p, dict) or set(p) != keys or p['driver'] != 'it8613' or p['module_version'] != DRIVER_VERSION:
        raise ValueError('调速配置不是已支持的驱动。')
    if not all(isinstance(p[k], str) and p[k] for k in keys - {'mapping'}):
        raise ValueError('调速配置字段错误。')
    if not p['device_path'].startswith('/sys/devices/') or '..' in Path(p['device_path']).parts:
        raise ValueError('控制器标识错误。')
    mapping = p['mapping']
    if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 5:
        raise ValueError('需要已验证的 PWM 与风扇对应关系。')
    for channel, fan in mapping.items():
        if not isinstance(channel, str) or not re.fullmatch(r'pwm[1-5]', channel) or not isinstance(fan, str) or not re.fullmatch(r'fan[1-5]', fan):
            raise ValueError('风扇通道格式错误。')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('不能将两个 PWM 绑定同一个反馈通道。')
    return p


def read_profile():
    info = PROFILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('调速配置必须由 root 持有，且普通用户不能修改。')
    return validate_profile(json.loads(PROFILE.read_text()))


class Hardware:
    def __init__(self, profile):
        self.profile = profile

    def chip(self):
        p = self.profile
        if collect.read('/sys/module/it87/version') != p['module_version']:
            raise ValueError('it87 驱动版本已变化，暂停调速。')
        for key in ('board_vendor', 'board_name'):
            if collect.read(Path('/sys/class/dmi/id') / key) != p[key]:
                raise ValueError('主板已变化，暂停调速。')
        matches = set()
        for h in Path('/sys/class/hwmon').glob('hwmon*'):
            real = h.resolve()
            parent = real.parent.parent if real.parent.name == 'hwmon' else real.parent
            if str(parent) == p['device_path'] and collect.read(h / 'name') == p['driver']:
                matches.add(real)
        if len(matches) != 1:
            raise ValueError('原风扇控制器已消失或标识不唯一。')
        return matches.pop()

    def read(self, channel):
        h = self.chip()
        fan = self.profile['mapping'][channel]
        return {'mode': integer(h / (channel + '_enable')), 'raw': integer(h / channel),
                'rpm': integer(h / (fan + '_input')),
                'fault': collect.read(h / (fan + '_fault')) == '1',
                'disabled': collect.read(h / (fan + '_enable')) == '0'}

    def write(self, channel, attribute, value):
        require_verified_recovery()
        if channel not in self.profile['mapping'] or attribute not in ('enable', 'pwm'):
            raise ValueError('禁止写入未配置通道。')
        if type(value) is not int or not 0 <= value <= (2 if attribute == 'enable' else 255):
            raise ValueError('风扇输出参数错误。')
        h = self.chip()
        name = channel + '_enable' if attribute == 'enable' else channel
        # dir_fd pins the controller directory across a possible hot removal.
        directory = os.open(h, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                data = (str(value) + '\n').encode()
                if os.write(fd, data) != len(data): raise OSError('Short fan control write')
            finally: os.close(fd)
        finally: os.close(directory)

    def temperature(self):
        return collect.temperatures()['cpu_temp_c']


class Controller:
    def __init__(self, profile, hardware=None, state=STATE, clock=time.monotonic):
        self.profile = validate_profile(profile)
        self.hw = hardware or Hardware(profile)
        self.state, self.clock = state, clock
        self.active = {}
        self.errors = {}

    def journal(self):
        save_json(self.state, {'profile': self.profile, 'channels': sorted(self.active)})

    def recover(self):
        if not self.state.exists(): return
        saved = json.loads(self.state.read_text())
        if saved.get('profile') != self.profile or not isinstance(saved.get('channels'), list):
            raise ValueError('有未恢复的旧调速记录，需先核对原控制器。')
        if any(c not in self.profile['mapping'] for c in saved['channels']):
            raise ValueError('调速恢复记录含未知通道。')
        self.active = {c: {'deadline': 0} for c in saved['channels']}
        for channel in list(self.active): self.restore(channel)
        if self.active: raise ValueError('风扇自动模式恢复失败，保留记录并停止新调速。')

    def restore(self, channel, reason='已恢复自动模式'):
        # Only restore channels that this broker took over (or journaled).
        if channel not in self.active: return
        try:
            self.hw.write(channel, 'enable', 2)
            if self.hw.read(channel)['mode'] != 2:
                raise ValueError('自动模式读回不一致')
        except (OSError, ValueError) as exc:
            try: self.hw.write(channel, 'enable', 0)  # pinned driver: full speed
            except (OSError, ValueError): pass
            self.active[channel]['deadline'] = 0
            self.errors[channel] = '自动模式恢复失败，已尝试全速保护：' + str(exc)
            return
        del self.active[channel]
        self.errors[channel] = reason
        self.journal()

    def healthy(self):
        temp = self.hw.temperature()
        if not isinstance(temp, (int, float)) or not math.isfinite(temp) or not -20 <= temp < 80:
            raise ValueError('CPU 温度不可用或已达到 80°C，停止手动调速。')

    def apply(self, channel, percent):
        require_verified_recovery()
        if channel not in self.profile['mapping'] or type(percent) is not int or not 60 <= percent <= 100:
            raise ValueError('只允许已验证通道，输出范围为 60–100%。')
        self.healthy()
        reading = self.hw.read(channel)
        if reading['fault'] or reading['disabled'] or reading['rpm'] < 300:
            raise ValueError('转速反馈异常或低于 300 RPM，不能进入手动调速。')
        if channel in self.active:
            raise ValueError('该通道已在手动控制中；先恢复自动模式再调整。')
        if reading['mode'] != 2:
            raise ValueError('该通道不是自动模式，可能有其他调速程序正在控制。')
        # Validate firmware automatic curve before taking ownership.
        self.hw.write(channel, 'enable', 2)
        if self.hw.read(channel)['mode'] != 2:
            raise ValueError('自动模式检查失败。')
        now = self.clock()
        lease = {'token': secrets.token_hex(16), 'deadline': now + LEASE_SECONDS,
                 'percent': percent, 'started': now, 'stalled_since': None}
        self.active[channel] = lease
        try:
            self.journal()  # Durable recovery record BEFORE any manual write.
        except OSError:
            del self.active[channel]
            raise
        try:
            self.hw.write(channel, 'enable', 0)  # full duty BEFORE manual takeover
            self.hw.write(channel, 'enable', 1)
            self.hw.write(channel, 'pwm', round(percent * 255 / 100))
            check = self.hw.read(channel)
            if check['mode'] not in (0, 1) or abs(check['raw'] - round(percent * 255 / 100)) > 2:
                raise ValueError('手动输出读回不一致。')
        except (OSError, ValueError):
            self.restore(channel, '设置失败，已恢复自动模式')
            raise
        self.errors.pop(channel, None)
        return {'token': lease['token'], 'expires_in': LEASE_SECONDS}

    def tick(self):
        now = self.clock()
        for channel, lease in list(self.active.items()):
            try:
                if now >= lease['deadline']: raise ValueError('连接中断或控制到期')
                self.healthy()
                value = self.hw.read(channel)
                expected = round(lease['percent'] * 255 / 100)
                if value['mode'] not in (0, 1) or abs(value['raw'] - expected) > 2:
                    raise ValueError('检测到其他控制程序或输出变化')
                if value['fault'] or value['disabled']: raise ValueError('风扇反馈故障')
                if value['rpm'] < 300:
                    lease['stalled_since'] = lease['stalled_since'] or now
                    if now - lease['stalled_since'] >= 4: raise ValueError('风扇转速过低')
                else: lease['stalled_since'] = None
            except (OSError, ValueError) as exc:
                self.restore(channel, str(exc) + '，已退出手动控制')

    def status(self):
        channels = []
        for channel, fan in self.profile['mapping'].items():
            try:
                value = self.hw.read(channel)
                error = self.errors.get(channel, '')
            except (OSError, ValueError) as exc:
                value = {'mode': None, 'rpm': None, 'raw': None}; error = str(exc)
            lease = self.active.get(channel)
            channels.append(dict(value, channel=channel, fan=fan, error=error,
                                 controlled=lease is not None,
                                 expires_in=max(0, round(lease['deadline'] - self.clock())) if lease else 0))
        return {'available': False, 'reason': '自动恢复实测失败，调速暂停', 'channels': channels, 'min_percent': 60, 'max_percent': 100}

    def request(self, data):
        if not isinstance(data, dict): raise ValueError('请求格式错误。')
        action = data.get('action')
        fields = {'status': {'action'}, 'apply': {'action', 'channel', 'percent'},
                  'auto': {'action', 'channel'}, 'renew': {'action', 'channel', 'token'}}
        if not isinstance(action, str) or action not in fields or set(data) != fields[action]: raise ValueError('请求字段错误。')
        if action == 'status': return self.status()
        channel = data['channel']
        if not isinstance(channel, str) or channel not in self.profile['mapping']:
            raise ValueError('未授权的风扇通道。')
        if action == 'apply': return self.apply(channel, data['percent'])
        if action == 'auto':
            self.restore(channel)
            if channel in self.active: raise ValueError(self.errors[channel])
            return {'ok': True}
        lease = self.active.get(channel)
        token = data['token']
        if not lease or not isinstance(token, str) or not secrets.compare_digest(token, lease.get('token', '')):
            raise ValueError('手动调速已结束，请重新设置。')
        if self.clock() >= lease['deadline']:
            self.restore(channel, '控制到期，已恢复自动模式')
            raise ValueError('手动调速已到期。')
        lease['deadline'] = self.clock() + LEASE_SECONDS
        return {'ok': True}


def configure(mappings):
    require_verified_recovery()
    if os.geteuid() != 0: raise ValueError('启用调速需要 sudo。')
    if PROFILE.exists(): raise ValueError('已有调速配置，未覆盖。')
    chips = [h for h in Path('/sys/class/hwmon').glob('hwmon*') if collect.read(h / 'name') == 'it8613']
    if len(chips) != 1: raise ValueError('需要恰好一个 IT8613 控制器。')
    h = chips[0].resolve()
    device = h.parent.parent if h.parent.name == 'hwmon' else h.parent
    mapping = {}
    for value in mappings:
        parts = value.split(':')
        if len(parts) != 2 or parts[0] in mapping: raise ValueError('格式示例：--map pwm2:fan2；不要重复通道。')
        mapping[parts[0]] = parts[1]
    profile = validate_profile({'driver': 'it8613', 'device_path': str(device),
               'module_version': DRIVER_VERSION, 'mapping': mapping,
               'board_vendor': collect.read('/sys/class/dmi/id/board_vendor'),
               'board_name': collect.read('/sys/class/dmi/id/board_name')})
    hw = Hardware(profile)
    for channel in mapping:
        value = hw.read(channel)
        if value['mode'] != 2 or value['fault'] or value['disabled'] or value['rpm'] < 300:
            raise ValueError('通道需要处于自动模式并提供有效转速。')
    # O_EXCL preserves any existing profile, including a concurrent setup.
    fd = os.open(PROFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(profile, output); output.flush(); os.fsync(output.fileno())


def main():
    require_verified_recovery()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure', action='store_true')
    parser.add_argument('--map', action='append', default=[])
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0: raise ValueError('Fan broker requires root.')
    if args.configure:
        configure(args.map); return
    profile = read_profile()
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime = Path(SOCKET).parent
    runtime.mkdir(mode=0o750, parents=True, exist_ok=True)
    lock = os.open(runtime / 'broker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    controller = Controller(profile)
    controller.recover()
    if args.restore: return
    account = pwd.getpwnam('nas-display-host')
    os.chown(runtime, 0, account.pw_gid); os.chmod(runtime, 0o750)
    def stop(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    if os.path.lexists(SOCKET): os.unlink(SOCKET)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(SOCKET); os.chown(SOCKET, 0, account.pw_gid); os.chmod(SOCKET, 0o660)
            server.listen(4); server.settimeout(.5)
            while True:
                controller.tick()
                try: client, _ = server.accept()
                except socket.timeout: continue
                with client:
                    client.settimeout(.5)
                    uid = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
                    if uid not in (0, account.pw_uid): continue
                    try:
                        data = b''
                        while not data.endswith(b'\n') and len(data) <= 2048:
                            chunk = client.recv(2049 - len(data))
                            if not chunk: break
                            data += chunk
                        if len(data) > 2048 or not data.endswith(b'\n'): raise ValueError('请求过大或不完整。')
                        result = controller.request(json.loads(data))
                    except (OSError, ValueError) as exc:
                        result = {'error': str(exc)}
                    try: client.sendall(json.dumps(result, ensure_ascii=False).encode() + b'\n')
                    except OSError: pass
    except KeyboardInterrupt:
        pass
    finally:
        for channel in list(controller.active): controller.restore(channel, '服务停止，已恢复自动模式')
        if os.path.lexists(SOCKET): os.unlink(SOCKET)
        os.close(lock)


if __name__ == '__main__':
    main()
