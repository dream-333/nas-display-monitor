"""Private settings, independent sensor sampling and USB / UDP delivery."""
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
import time
from collections import deque

# In the package these modules live next to this file; in a checkout use src/collector.
import sys
if (Path(__file__).resolve().parent.name == 'host'
        and Path(__file__).resolve().parents[1].name == 'src'
        and (Path(__file__).resolve().parents[1] / 'collector').is_dir()):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
import collect
import send
from usb_link import UsbLink, TransferCancelled

VERSION = '1.5.1'
PASSWORD_ROUNDS = 600_000
CONFIG_KEYS = {'display_ip', 'port', 'token', 'interface', 'interval', 'gpu_device', 'disks', 'enabled', 'transport', 'usb_port', 'sys_sensor', 'cpu_sensor'}


def validate_config(value):
    if isinstance(value, dict) and 'cpu_sensor' not in value:
        value = dict(value, cpu_sensor='auto')
    if isinstance(value, dict) and 'sys_sensor' not in value:
        value = dict(value, sys_sensor='auto')
    if not isinstance(value, dict) or set(value) != CONFIG_KEYS:
        raise ValueError('配置字段不完整或包含未知字段。')
    cfg = copy.deepcopy(value)
    if not isinstance(cfg['cpu_sensor'], str) or not (cfg['cpu_sensor'] in ('', 'auto') or re.fullmatch(r'cpu-[0-9a-f]{24}', cfg['cpu_sensor'])):
        raise ValueError('请选择已发现的 CPU 温度通道。')
    if not isinstance(cfg['sys_sensor'], str) or not (cfg['sys_sensor'] in ('', 'auto') or re.fullmatch(r'board-[0-9a-f]{24}', cfg['sys_sensor'])):
        raise ValueError('请选择已发现的 SYS 温度通道。')
    if cfg['transport'] not in ('usb', 'udp'):
        raise ValueError('请选择 USB 或 Wi-Fi 连接。')
    if not isinstance(cfg['usb_port'], str) or not (cfg['usb_port'] == 'auto' or re.fullmatch(r'/dev/(?:ttyACM[0-9]+|serial/by-id/[A-Za-z0-9_.:+-]+)', cfg['usb_port'])):
        raise ValueError('USB 端口格式不正确。')
    if type(cfg['enabled']) is not bool:
        raise ValueError('发送开关必须是布尔值。')
    if not isinstance(cfg['display_ip'], str):
        raise ValueError('屏幕地址必须是 IPv4 地址。')
    if cfg['display_ip']:
        try:
            address = ipaddress.IPv4Address(cfg['display_ip'])
        except ipaddress.AddressValueError:
            raise ValueError('屏幕地址必须是 IPv4 地址。') from None
        if address.is_multicast or address.is_unspecified or str(address) == '255.255.255.255':
            raise ValueError('请输入单台屏幕的 IPv4 地址。')
    if type(cfg['port']) is not int or not 1 <= cfg['port'] <= 65535:
        raise ValueError('UDP 端口必须是 1–65535 的整数。')
    if type(cfg['interval']) not in (int, float) or not math.isfinite(cfg['interval']) or not .2 <= cfg['interval'] <= 5:
        raise ValueError('采集间隔必须是 0.2–5 秒。')
    if not isinstance(cfg['token'], str) or not re.fullmatch(r'[0-9a-f]{32}', cfg['token']):
        raise ValueError('配对码必须是 32 位小写十六进制字符，与屏幕保持一致。')
    for key, limit in [('interface', 32), ('gpu_device', 100)]:
        if not isinstance(cfg[key], str) or len(cfg[key]) > limit or any(c.isspace() or ord(c) < 32 for c in cfg[key]):
            raise ValueError('网卡或 GPU 标识格式错误。')
    if not isinstance(cfg['disks'], list) or len(cfg['disks']) != 2 or any(
            not isinstance(v, str) or len(v) > 200 or any(ord(c) < 32 for c in v) for v in cfg['disks']):
        raise ValueError('需要两个 SSD 型号字段；不显示的项目可留空。')
    if cfg['enabled'] and (not cfg['interface'] or (cfg['transport'] == 'udp' and not cfg['display_ip'])):
        raise ValueError('启用发送前，请选择网卡；Wi-Fi 模式还需填写屏幕 IP。')
    return cfg


def default_config():
    names = sorted(collect.network())
    # Prefer hardware interfaces; never sum a bridge and its underlying port.
    physical = [n for n in names if (Path('/sys/class/net') / n / 'device').exists()]
    disks = [d['model'] for d in collect.temperatures()['nvme'] if d.get('model')]
    return {'display_ip': '', 'port': 44445, 'token': secrets.token_hex(16),
            'interface': next(iter(physical or names), ''), 'interval': 2,
            'gpu_device': 'auto', 'sys_sensor': 'auto', 'cpu_sensor': 'auto',
            'disks': (disks + ['', ''])[:2], 'enabled': False, 'transport': 'usb', 'usb_port': 'auto'}


def atomic_json(path, data):
    """Replace within the same directory; neither temporary nor final file is public."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def password_record(password, previous=None):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise ValueError('管理员密码需要 8–128 个字符。')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
    return {'salt': salt, 'digest': digest, 'rounds': PASSWORD_ROUNDS,
            'secret': previous['secret'] if previous else secrets.token_hex(32),
            'generation': secrets.token_hex(16)}


def check_password(password, auth):
    if not isinstance(password, str) or len(password) > 128:
        return False
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(auth['salt']), auth['rounds']).hex()
    return hmac.compare_digest(digest, auth['digest'])


class Settings:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.revision = 0
        self.config = validate_config(json.loads((self.directory / 'config.json').read_text()))

    def get(self):
        with self.lock:
            return copy.deepcopy(self.config)

    def save(self, cfg):
        cfg = validate_config(cfg)
        with self.lock:
            atomic_json(self.directory / 'config.json', cfg)
            self.config = cfg
            self.revision += 1
        return self.get()

    def auth(self):
        with self.lock:
            return json.loads((self.directory / 'auth.json').read_text())

    def change_password(self, old, new):
        with self.lock:
            auth = self.auth()
            if not check_password(old, auth):
                raise ValueError('当前密码不正确。')
            atomic_json(self.directory / 'auth.json', password_record(new, auth))


class Monitor:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.send_wake = threading.Event()
        self.thread = None
        self.sender_thread = None
        self.sample = None
        self.sample_at = None
        self.last_sent = None
        self.last_usb_ack = None
        self.sent = 0
        self.error = None
        self.sample_error = None
        self.usb = UsbLink()
        self.events = deque(maxlen=30)
        self.started = time.time()

    def event(self, message):
        with self.lock:
            self.events.appendleft({'time': time.time(), 'message': message})

    def configuration_changed(self):
        with self.lock:
            self.last_usb_ack = None
            self.error = None
        self.wake.set()
        self.send_wake.set()

    def start(self):
        if self.thread is None:
            self.sender_thread = threading.Thread(target=self.send_loop, name='nas-sender', daemon=True)
            self.thread = threading.Thread(target=self.run, name='nas-sampler', daemon=True)
            self.sender_thread.start()
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        self.send_wake.set()
        for thread in (self.thread, self.sender_thread):
            if thread:
                thread.join(timeout=7)
        # Only the sender owns the serial descriptor while it is running.
        if self.sender_thread is None:
            self.usb.close()

    def run(self):
        previous = None
        self.event('后台采集已启动')
        while not self.stop_event.is_set():
            self.wake.clear()
            try:
                current = collect.snapshot()
                sample = collect.report(previous, current, None) if previous is not None else None
                previous = current
                with self.lock:
                    self.sample_error = None
                    if sample is not None:
                        self.sample, self.sample_at = sample, time.monotonic()
                if sample is not None:
                    self.send_wake.set()
            except (OSError, ValueError, RuntimeError, KeyError, IndexError):
                previous = None
                with self.lock:
                    message = '采集失败，请检查主机传感器权限。'
                    if self.sample_error != message:
                        self.events.appendleft({'time': time.time(), 'message': message})
                    self.sample_error = message
            self.wake.wait(self.settings.get()['interval'])

    def send_loop(self):
        # Keep only the latest sample: reconnect must never replay a backlog.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(.5)
                while not self.stop_event.is_set():
                    self.send_wake.wait()
                    self.send_wake.clear()
                    if self.stop_event.is_set():
                        break
                    with self.settings.lock:
                        cfg, revision = self.settings.get(), self.settings.revision
                    with self.lock:
                        sample = copy.deepcopy(self.sample)
                        sample_error = self.sample_error

                    def cancelled():
                        with self.settings.lock:
                            return self.stop_event.is_set() or self.settings.revision != revision

                    try:
                        if not cfg['enabled']:
                            self.usb.close()
                            continue
                        if sample is None or sample_error:
                            continue
                        if cfg['interface'] not in sample['network']:
                            raise ValueError('选择的网卡当前不存在，请重新选择。')
                        if cfg['transport'] == 'usb':
                            def latest_packet():
                                with self.lock:
                                    latest = copy.deepcopy(self.sample)
                                    error = self.sample_error
                                if error:
                                    raise ValueError(error)
                                if cfg['interface'] not in latest['network']:
                                    raise ValueError('选择的网卡当前不存在，请重新选择。')
                                return send.packet(latest, cfg)
                            self.usb.send(latest_packet, cfg['usb_port'], cancelled)
                        else:
                            self.usb.close()
                            with self.settings.lock:
                                if cancelled():
                                    raise TransferCancelled()
                                sock.sendto(send.packet(sample, cfg), (cfg['display_ip'], cfg['port']))
                        # A response for a superseded configuration is not a current ACK.
                        with self.settings.lock:
                            if cancelled():
                                raise TransferCancelled()
                            with self.lock:
                                if self.error:
                                    self.events.appendleft({'time': time.time(), 'message': '连接已恢复，继续发送数据'})
                                self.error = None
                                self.sent += 1
                                self.last_sent = time.time()
                                self.last_usb_ack = self.last_sent if cfg['transport'] == 'usb' else None
                    except TransferCancelled:
                        continue
                    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
                        with self.settings.lock:
                            if cancelled():
                                continue
                            message = str(exc) if isinstance(exc, ValueError) else '发送失败，请检查设备连接与权限。'
                            with self.lock:
                                if self.error != message:
                                    self.events.appendleft({'time': time.time(), 'message': message})
                                self.error = message
                                self.last_usb_ack = None
        finally:
            self.usb.close()

    def status(self):
        from hardware_status import hardware_status
        cfg = self.settings.get()
        with self.lock:
            sample = copy.deepcopy(self.sample)
            result = {'version': VERSION, 'hostname': socket.gethostname(),
                      'enabled': cfg['enabled'], 'display_ip': cfg['display_ip'], 'transport': cfg['transport'],
                      'interface': cfg['interface'], 'interval': cfg['interval'],
                      'sent': self.sent, 'last_sent': self.last_sent,
                      'uptime_seconds': int(time.time() - self.started), 'error': self.sample_error or self.error,
                      'sampling_error': self.sample_error,
                      'display_error': self.error if cfg['enabled'] else None,
                      'sample_age': round(time.monotonic() - self.sample_at, 1) if self.sample_at else None,
                      'events': list(self.events), 'sample': sample, 'metrics': None,
                      'receiver_confirmed': cfg['enabled'] and cfg['transport'] == 'usb' and self.last_usb_ack is not None and time.time() - self.last_usb_ack < 10 and not (self.sample_error or self.error)}
        result['hardware_setup'] = hardware_status()
        if sample:
            metrics = json.loads(send.packet(sample, cfg))
            metrics.pop('token')
            metrics.pop('v')
            result['metrics'] = metrics
            gpu = send.select_gpu(sample, cfg)
            notes = []
            cpu = collect.select_cpu_temperature(sample, cfg.get('cpu_sensor', 'auto'))
            result['cpu_temperature'] = cpu
            if cpu['status'] == 'disabled':
                notes.append('CPU 温度显示已关闭。')
            elif cpu['status'] == 'missing':
                notes.append('所选 CPU 温度通道已不存在，请在屏幕与采集中重新选择。')
            elif cpu['value_c'] is None:
                notes.append('CPU 温度不可读：请检查所选通道、驱动及传感器权限。')
            elif cpu.get('driver'):
                notes.append('CPU 温度：' + cpu['driver'] + ' / ' + cpu['label'])
            if not gpu:
                notes.append('GPU 显示已关闭，可在配置中选择自动识别。' if cfg['gpu_device'] == '' else '未找到所选 GPU，请选择自动识别或当前设备。')
            else:
                if cfg['gpu_device'] not in ('auto', gpu['device']):
                    notes.append('旧 GPU 地址已失效，已使用当前唯一 GPU；建议保存为自动识别。')
                if gpu.get('temperature_c', gpu.get('edge_c')) is None:
                    notes.append('GPU 温度不可读，请检查传感器权限或读数。' if gpu.get('temperature_status') == 'unreadable' else 'GPU 驱动未提供可用的独立温度；不会以 CPU 温度替代。')
                elif gpu.get('temperature_source'):
                    notes.append('GPU 温度：' + gpu['temperature_source'])
                if gpu.get('busy_percent') is None:
                    status = gpu.get('busy_status')
                    notes.append({'permission_denied': 'Intel GPU 性能计数器权限不足：请检查服务的 CAP_PERFMON 权限。',
                                  'warming_up': 'GPU 占用率正在建立采样，请等待下一次刷新。',
                                  'not_exposed': '内核未提供可匹配的 i915 PMU 引擎计数器，GPU 温度采集不受影响。',
                                  'unavailable': 'Intel GPU 性能计数器读取失败：' + gpu.get('busy_error', '')}.get(
                                      status, 'GPU 占用率接口暂不可用，温度采集不受影响。'))
                elif gpu.get('busy_source') == 'i915 PMU':
                    notes.append('GPU 占用率：i915 PMU 各引擎最高值（含视频编解码），不是引擎百分比相加。')
            from board import select_system
            result['system_temperature'] = select_system(sample, cfg.get('sys_sensor', 'auto'))
            result['selected_gpu'] = gpu
            result['sensor_notes'] = notes
        return result
