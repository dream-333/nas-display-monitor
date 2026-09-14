#!/usr/bin/env python3
"""Local fan broker entrypoint and pinned IT8613 recovery adapter.

State is fsynced before the first hardware write. In this pinned driver manual
PWM aliases auto_start; returning to auto restores that value before mode=2.
The withdrawn fan_service.py draft is not used by this implementation.
Common driver contracts are implemented in fan_hwmon.py.
"""
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import struct
import tempfile
import time
import sys

# The service uses -I. Only add its root-owned packaged module directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

VERSION = 'nasdisplay-a904dd88'
CHANNELS = {'pwm2': 'fan2', 'pwm3': 'fan3'}
CURVE = ('auto_channels_temp', 'auto_point1_temp', 'auto_point1_temp_hyst',
         'auto_point2_temp', 'auto_point3_temp', 'auto_slope', 'freq')
ACCOUNT = os.environ.get('NAS_DISPLAY_FAN_ACCOUNT', 'nas-display-fnos')
STATE = Path(os.environ.get('NAS_DISPLAY_FAN_STATE', '/var/lib/nas-display-fnos-fan/active.json'))
SOCKET = os.environ.get('NAS_DISPLAY_FAN_SOCKET', '/run/nas-display-fnos-fan/control.sock')


def atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.fan-')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read(path):
    return Path(path).read_text().strip()


def number(path):
    text = read(path)
    if not re.fullmatch(r'-?[0-9]+', text): raise ValueError('传感器读数无效。')
    return int(text)


def validate_request(data):
    if data == {'action': 'status'}: return data
    if isinstance(data, dict) and set(data) == {'action', 'channel', 'curve'} and data['action'] == 'curve':
        from fan_client import valid_channel
        from fan_curve import validate
        if not valid_channel(data['channel']): raise ValueError('无效风扇通道。')
        return dict(data, curve=validate(data['curve']))
    if not isinstance(data, dict) or set(data) != {'action', 'channel', 'mode', 'pwm'} or data['action'] != 'set':
        raise ValueError('风扇请求格式错误。')
    if not isinstance(data['channel'], str) or (data['channel'] not in CHANNELS and not re.fullmatch(r'hwmon-[0-9a-f]{16}-pwm[1-9][0-9]?', data['channel'])) or data['mode'] not in ('auto', 'manual'):
        raise ValueError('请选择已发现的风扇通道和自动 / 手动模式。')
    if data['mode'] == 'manual':
        if type(data['pwm']) is not int or not 0 <= data['pwm'] <= 255:
            raise ValueError('PWM 必须是 0–255 的整数。')
    elif data['pwm'] is not None:
        raise ValueError('自动模式不接受手动 PWM 值。')
    return data


class Hardware:
    def __init__(self, sys=Path('/sys'), boot=Path('/proc/sys/kernel/random/boot_id')):
        self.sys, self.boot = sys, boot

    def locate(self):
        if read(self.sys / 'module/it87/version') != VERSION:
            raise ValueError('当前 it87 驱动版本未验证，禁止写入。')
        matches = [h.resolve() for h in (self.sys / 'class/hwmon').glob('hwmon*') if read(h / 'name') == 'it8613']
        if len(matches) != 1: raise ValueError('IT8613 控制器不存在或不唯一。')
        h = matches[0]
        controller = h.parent.parent if h.parent.name == 'hwmon' else h.parent
        info = h.stat()
        return h, {'boot': read(self.boot), 'device': str(controller), 'inode': info.st_ino, 'driver': VERSION}

    def read(self, channel):
        if channel not in CHANNELS: raise ValueError('未验证的风扇通道。')
        h, identity = self.locate()
        mode, pwm, start = number(h / (channel + '_enable')), number(h / channel), number(h / (channel + '_auto_start'))
        if mode not in (0, 1, 2) or not 0 <= pwm <= 255 or not 0 <= start <= 255:
            raise ValueError('风扇控制器状态无效。')
        fan = CHANNELS[channel]
        alarm = number(h / (fan + '_alarm')) if (h / (fan + '_alarm')).exists() else None
        temp_map = number(h / (channel + '_auto_channels_temp'))
        if temp_map not in (1, 2, 4): raise ValueError('温度映射不在已验证范围，禁止切换模式。')
        sensor = {1: 1, 2: 2, 4: 3}[temp_map]
        return {'identity': identity, 'mode_raw': mode, 'pwm': pwm, 'auto_start': start,
                'curve': {k: number(h / (channel + '_' + k)) for k in CURVE},
                'rpm': number(h / (fan + '_input')), 'alarm': alarm,
                'temperature_c': number(h / f'temp{sensor}_input') / 1000}

    def write(self, channel, field, value, identity):
        if channel not in CHANNELS or field not in ('enable', 'pwm', 'auto_start'):
            raise ValueError('禁止写入该控制节点。')
        if type(value) is not int or not 0 <= value <= (2 if field == 'enable' else 255):
            raise ValueError('写入值超出范围。')
        if field == 'enable' and value not in (1, 2): raise ValueError('不使用会覆盖起始值的 enable=0。')
        h, current = self.locate()
        if current != identity: raise ValueError('控制器已更换或驱动重新加载，停止写入。')
        directory = os.open(h, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(directory).st_ino != identity['inode']: raise ValueError('控制器已发生变化。')
            name = channel if field == 'pwm' else channel + '_' + field
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                payload = (str(value) + '\n').encode()
                if os.write(fd, payload) != len(payload): raise OSError('Short write')
            finally: os.close(fd)
        finally: os.close(directory)


class Controller:
    def __init__(self, hardware=None, state=STATE, sleep=time.sleep, boot=None):
        self.hw, self.path, self.sleep = hardware or Hardware(), state, sleep
        self.boot = boot or read('/proc/sys/kernel/random/boot_id')
        self.entries, self.errors = {}, {}
        if state.exists():
            data = json.loads(state.read_text())
            if not isinstance(data, dict) or set(data) != {'boot', 'entries'} or not isinstance(data['entries'], dict):
                raise ValueError('风扇恢复记录损坏，禁止写入。')
            if data['boot'] != self.boot:
                # BIOS may have initialized new curves. Never restore a previous boot's values.
                atomic(state.with_name('previous-boot.json'), data)
                self.save()
            else:
                self.entries = data['entries']
                for channel, entry in self.entries.items():
                    if channel not in CHANNELS or set(entry) != {'identity', 'auto_start', 'curve'} or set(entry['curve']) != set(CURVE):
                        raise ValueError('风扇恢复记录损坏，禁止写入。')
                    if type(entry['auto_start']) is not int or not 0 <= entry['auto_start'] <= 255:
                        raise ValueError('保存的自动起始值无效。')

    @property
    def error(self):
        return '；'.join(k + '：' + v for k, v in sorted(self.errors.items())) or None

    def save(self):
        atomic(self.path, {'boot': self.boot, 'entries': self.entries})

    def status(self):
        channels = []
        for channel, fan in CHANNELS.items():
            try:
                d = self.hw.read(channel)
                known = channel in self.entries
                channels.append({'channel': channel, 'fan': fan, 'mode': 'auto' if d['mode_raw'] == 2 else 'manual',
                                 'pwm': d['pwm'] if d['mode_raw'] != 2 else None, 'register': d['pwm'],
                                 'rpm': d['rpm'], 'alarm': d['alarm'], 'temperature_c': d['temperature_c'],
                                 'can_set': d['mode_raw'] == 2 or known,
                                 'can_auto': d['mode_raw'] == 2 or known,
                                 'note': '自动模式下该寄存器是起始 PWM，不是实时占空比。' if d['mode_raw'] == 2 else
                                         '手动 PWM 固定，不随温度调整。' if known else '当前已是手动模式，缺少原自动参数快照，不能接管。'})
            except (OSError, ValueError):
                return {'available': False, 'error': self.error or '当前主板、驱动或风扇接口未通过调速检查。', 'channels': []}
        return {'available': True, 'error': self.error, 'channels': channels}

    def consistent(self, channel):
        d = self.hw.read(channel); e = self.entries[channel]
        if d['identity'] != e['identity'] or d['curve'] != e['curve']:
            raise ValueError('控制器或自动温控参数已被其他程序改变，停止覆盖。')
        return d, e

    def restore(self, channel):
        d, e = self.consistent(channel)
        if d['mode_raw'] == 2:
            if d['auto_start'] != e['auto_start']:
                raise ValueError('自动模式参数已被其他程序改变，保留现场。')
        else:
            self.hw.write(channel, 'auto_start', e['auto_start'], e['identity'])
            # Check shared register restoration before enabling the automatic loop.
            self.sleep(1.6)
            check, _ = self.consistent(channel)
            if check['auto_start'] != e['auto_start'] or check['pwm'] != e['auto_start']:
                raise ValueError('自动起始 PWM 恢复读回不一致。')
            self.hw.write(channel, 'enable', 2, e['identity'])
            self.sleep(1.6)
            check, _ = self.consistent(channel)
            if check['mode_raw'] != 2 or check['auto_start'] != e['auto_start']:
                raise ValueError('自动模式恢复读回不一致。')
        # RPM and alarms are always reported, including a legitimate low-temp stop.
        del self.entries[channel]
        try: self.save()
        except OSError:
            self.entries[channel] = e
            raise
        self.errors.pop(channel, None)

    def recover(self):
        for channel in list(self.entries):
            try: self.restore(channel)
            except (OSError, ValueError) as exc: self.errors[channel] = str(exc) if isinstance(exc, ValueError) else '恢复写入失败'

    def apply(self, channel, mode, pwm):
        validate_request({'action': 'set', 'channel': channel, 'mode': mode, 'pwm': pwm})
        d = self.hw.read(channel)
        if mode == 'auto':
            if channel in self.entries:
                try: self.restore(channel)
                except (OSError, ValueError) as exc:
                    self.errors[channel] = str(exc) if isinstance(exc, ValueError) else '自动模式恢复失败，快照已保留。'
                    raise ValueError(self.errors[channel]) from None
            elif d['mode_raw'] != 2: raise ValueError('缺少原自动参数快照，不能猜测自动起始 PWM。')
            self.errors.pop(channel, None)
            return self.status()
        if channel in self.errors:
            raise ValueError('此通道仍有未完成的恢复，请先应用自动模式。')
        if channel not in self.entries:
            if d['mode_raw'] != 2 or d['auto_start'] != d['pwm']:
                raise ValueError('请先确保风扇处于原自动模式，再由本应用切换手动。')
            curve = d['curve']
            points = [curve[k] for k in ('auto_point1_temp', 'auto_point2_temp', 'auto_point3_temp')]
            if (not 0 <= d['temperature_c'] <= 125 or not 0 <= points[0] <= points[1] <= points[2] <= 125000
                    or not 0 <= curve['auto_point1_temp_hyst'] <= points[0]
                    or not 0 <= curve['auto_slope'] <= 127 or curve['freq'] <= 0):
                raise ValueError('当前温度或自动参数异常，保留原模式，不接管调速。')
            self.entries[channel] = {k: d[k] for k in ('identity', 'auto_start', 'curve')}
            try: self.save()  # Failure here must prevent ALL hardware writes.
            except BaseException:
                del self.entries[channel]
                raise
        d, e = self.consistent(channel)
        if d['mode_raw'] == 2 and d['auto_start'] != e['auto_start']:
            raise ValueError('自动起始参数已改变，停止覆盖。')
        try:
            if d['mode_raw'] == 2: self.hw.write(channel, 'enable', 1, e['identity'])
            self.hw.write(channel, 'pwm', pwm, e['identity'])
            self.sleep(1.6)  # it87 caches sensor data for about 1.5 seconds.
            check, _ = self.consistent(channel)
            if check['pwm'] != pwm or check['mode_raw'] not in ((0, 1) if pwm == 255 else (1,)):
                raise ValueError('手动模式/PWM 读回不一致。')
        except (OSError, ValueError):
            try: self.restore(channel)
            except (OSError, ValueError):
                self.errors[channel] = '设置失败且自动参数未完全恢复；快照已保留，请检查风扇反馈及服务日志。'
            raise ValueError(self.errors.get(channel) or '设置失败，已恢复原自动模式。') from None
        self.errors.pop(channel, None)
        return self.status()


def allowed(peer, uid):
    return peer in (0, uid)


def notify(message):
    path = os.environ.get('NOTIFY_SOCKET')
    if not path: return
    if path.startswith('@'): path = '\0' + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), path)


def main():
    if os.geteuid() != 0: raise SystemExit('Use the packaged fan broker service')
    account = pwd.getpwnam(ACCOUNT)
    STATE.parent.mkdir(exist_ok=True, mode=0o700)
    with (STATE.parent / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from fan_hwmon import Broker
        from fan_curve import Engine
        broker = Broker(Controller(), atomic); broker.recover()
        controller = Engine(broker, STATE.with_name('curves.json'), atomic)
        running = True
        def stop(*_):
            nonlocal running
            running = False
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        path = Path(SOCKET); path.parent.mkdir(exist_ok=True, mode=0o755)
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(SOCKET); os.chown(SOCKET, 0, grp.getgrnam(ACCOUNT).gr_gid); os.chmod(SOCKET, 0o660)
            server.listen(8); server.settimeout(.5)
            try:
                controller.resume()
                notify('READY=1')
                while running:
                    controller.tick()
                    notify('WATCHDOG=1')
                    try: conn, _ = server.accept()
                    except socket.timeout: continue
                    with conn:
                        conn.settimeout(1)
                        _, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                        if not allowed(uid, account.pw_uid): continue
                        try:
                            raw = b''
                            while not raw.endswith(b'\n') and len(raw) <= 2048:
                                chunk = conn.recv(2049 - len(raw))
                                if not chunk: break
                                raw += chunk
                            if len(raw) > 2048: raise ValueError('请求过大。')
                            data = validate_request(json.loads(raw))
                            if data['action'] == 'status': result = controller.status()
                            elif data['action'] == 'curve': result = controller.start(data['channel'], data['curve'])
                            else: result = controller.apply(data['channel'], data['mode'], data['pwm'])
                            response = {'ok': True, 'status': result}
                        except (OSError, ValueError, TypeError) as exc:
                            message = str(exc) if type(exc) is ValueError else '控制未完成，请检查模式、PWM、驱动与恢复快照。'
                            response = {'ok': False, 'error': message}
                        try: conn.sendall(json.dumps(response, ensure_ascii=False).encode() + b'\n')
                        except OSError: pass
            finally:
                controller.recover()
                if controller.error: print(controller.error, flush=True)
                path.unlink(missing_ok=True)


if __name__ == '__main__': main()
