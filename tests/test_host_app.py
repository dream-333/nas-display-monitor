"""Run with .venv/bin/python after installing src/host/requirements-dev.txt."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import pty
import socket
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

if not all(importlib.util.find_spec(n) for n in ('flask', 'waitress', 'serial')):
    raise unittest.SkipTest('Install src/host/requirements-dev.txt to run host application tests')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
from app import create_app
from core import Monitor, Settings, atomic_json, default_config, password_record, validate_config
from usb_link import UsbLink
import core

PASSWORD = 'test-admin-passphrase-only'
# Salted hash can be reused in independent temporary directories; no real credentials.
AUTH = password_record(PASSWORD)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.cfg = default_config()
        atomic_json(self.directory / 'config.json', self.cfg)
        atomic_json(self.directory / 'auth.json', AUTH)
        self.settings = Settings(self.directory)
        self.monitor = Monitor(self.settings)
        self.app = create_app(self.directory, self.monitor)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.get('/api/session').json['csrf']

    def tearDown(self):
        self.monitor.stop()
        self.tmp.cleanup()

    def post(self, path, data, client=None, csrf=None):
        return (client or self.client).post('/api/' + path, json=data, headers={'X-CSRF-Token': csrf or self.csrf})

    def login(self, client=None):
        client = client or self.client
        csrf = client.get('/api/session').json['csrf']
        r = self.post('login', {'password': PASSWORD}, client, csrf)
        self.assertEqual(r.status_code, 200)
        if client == self.client:
            self.csrf = r.json['csrf']
        return r.json['csrf']

    def test_fan_api_auth_validation_and_broker_isolation(self):
        with patch('fan_client.request', return_value={'available': True, 'channels': []}) as broker:
            self.assertEqual(self.client.get('/api/fans').status_code, 401)
            data = {'channel': 'pwm2', 'mode': 'manual', 'speed_percent': 50}
            self.assertEqual(self.post('fans', data).status_code, 401)
            self.login()
            self.assertEqual(self.client.post('/api/fans', json=data).status_code, 403)
            for bad in [{}, {**data, 'path': '/sys/evil'}, {**data, 'channel': 'pwm4'},
                        {**data, 'channel': []}, {**data, 'mode': 'full'},
                        *[{**data, 'speed_percent': v} for v in (-1, 101, 255, True, '50', 1.5, None)],
                        {**data, 'mode': 'auto'}, {'channel':'pwm2','mode':'manual','pwm':128}]:
                self.assertEqual(self.post('fans', bad).status_code, 400)
            broker.assert_not_called()
            self.assertEqual(self.post('fans', data).status_code, 200)
            broker.assert_called_once_with({'action': 'set', 'channel':'pwm2', 'mode':'manual', 'pwm':128})
            self.assertEqual(self.post('fans', {**data, 'mode':'auto', 'speed_percent':None}).status_code, 200)
            broker.assert_called_with({'action':'set','channel':'pwm2','mode':'auto','pwm':None})
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(self.client.get('/api/fans').json)

    def test_curve_api_auth_csrf_validation_and_no_client_temperatures(self):
        import fan_curve
        data={'channel':'pwm2','curve':fan_curve.preset()}
        with patch('fan_client.request',return_value={'available':True,'channels':[]}) as broker:
            self.assertEqual(self.post('fan-curve',data).status_code,401)
            self.login()
            self.assertEqual(self.client.post('/api/fan-curve',json=data).status_code,403)
            for bad in [dict(data,temperature=35),dict(data,channel='/sys/pwm1'),
                        dict(data,curve=dict(data['curve'],source='/sys/temp1_input')),
                        dict(data,curve=dict(data['curve'],points=[[30,60],[50,40]]))]:
                self.assertEqual(self.post('fan-curve',bad).status_code,400)
            broker.assert_not_called()
            self.assertEqual(self.post('fan-curve',data).status_code,200)
            broker.assert_called_once_with({'action':'curve',**data})

    def test_fan_percent_roundtrip_through_api_and_driver_readback(self):
        self.login()
        channel = 'hwmon-0123456789abcdef-pwm1'
        raw_values = []
        def broker(data):
            raw_values.append(data['pwm'])
            return {'available':True, 'channels':[{'channel':channel, 'mode':'manual',
                    'label':'nct6798 / pwm1', 'pwm':data['pwm'], 'register':data['pwm'], 'rpm':1380}]}
        with patch('fan_client.request', side_effect=broker):
            for percent in range(101):
                result = self.post('fans', {'channel':channel,'mode':'manual','speed_percent':percent})
                self.assertEqual(result.status_code,200)
                row = result.json['channels'][0]
                self.assertEqual(row['speed_percent'],percent)
                self.assertEqual(row['rpm'],1380)
                self.assertNotIn('pwm',row);self.assertNotIn('register',row)
                self.assertNotIn('pwm',row['label'])
        self.assertEqual([raw_values[i] for i in (0,1,50,99,100)], [0,3,128,252,255])
        self.assertEqual(raw_values,sorted(set(raw_values)))

    def test_fan_auto_does_not_present_start_register_as_speed(self):
        self.login()
        status = {'available':True,'channels':[{'channel':'pwm2','mode':'auto',
                  'pwm':None,'register':73,'rpm':0,'note':'自动模式下该寄存器是起始 PWM，不是实时占空比。'}]}
        with patch('fan_client.status', return_value=status):
            row = self.client.get('/api/fans').json['channels'][0]
        self.assertIsNone(row['speed_percent']);self.assertEqual(row['rpm'],0)
        self.assertNotIn('PWM',row['note']);self.assertNotIn('register',row)

    def test_auth_csrf_headers_and_private_data(self):
        for path in ('status', 'config', 'devices'):
            self.assertEqual(self.client.get('/api/' + path).status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'password': PASSWORD}).status_code, 403)
        self.login()
        r = self.client.get('/api/config')
        self.assertNotIn('token', r.json)
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
        self.assertNotIn('token', self.client.get('/api/status').json.get('metrics') or {})
        self.assertEqual(self.client.post('/api/config', json=self.cfg).status_code, 403)
        self.assertEqual(self.client.post('/api/config', data='{}', content_type='text/plain', headers={'X-CSRF-Token': self.csrf}).status_code, 415)
        self.assertEqual(self.post('logout', {}).status_code, 200)
        self.assertEqual(self.client.get('/api/status').status_code, 401)

    def test_password_reset_revokes_other_sessions_and_preserves_display_settings(self):
        self.login()
        other = self.app.test_client()
        self.login(other)
        self.assertEqual(self.post('password', {'current': PASSWORD, 'new': '1234567'}).status_code, 400)
        self.assertEqual(self.post('password', {'current': 'wrong', 'new': '12345678'}).status_code, 400)
        self.assertEqual(self.post('password', {'current': PASSWORD, 'new': '12345678'}).status_code, 200)
        self.assertEqual(other.get('/api/config').status_code, 401)
        self.assertEqual(self.settings.get(), self.cfg)
        csrf = self.client.get('/api/session').json['csrf']
        self.assertEqual(self.post('login', {'password': '12345678'}, csrf=csrf).status_code, 200)

    def test_login_rate_limit_and_password_not_in_storage(self):
        for _ in range(5):
            self.assertEqual(self.post('login', {'password': 'incorrect'}).status_code, 401)
        self.assertEqual(self.post('login', {'password': PASSWORD}).status_code, 429)
        self.assertNotIn(PASSWORD, (self.directory / 'auth.json').read_text())

    def test_config_validation_and_private_persistence(self):
        self.login()
        bad_values = [('interval', float('nan')), ('interval', float('inf')), ('interval', True),
                      ('interval', .1), ('port', True), ('port', 0), ('enabled', 'yes'),
                      ('disks', 'two'), ('disks', ['', None]),
                      ('transport', 'ftp'), ('usb_port', '/etc/passwd'), ('usb_port', '/dev/ttyS0'),
                      ('display_ip', '255.255.255.255'), ('display_ip', '224.0.0.1'), ('display_ip', 'example.com')]
        for key, value in bad_values:
            cfg = copy.deepcopy(self.cfg); cfg[key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.post('config', cfg).status_code, 400)
        cfg = self.settings.get();cfg['unknown'] = 'x'
        self.assertEqual(self.post('config', cfg).status_code, 400)
        cfg = self.settings.get();cfg.update(transport='udp', enabled=True, display_ip='')
        self.assertEqual(self.post('config', cfg).status_code, 400)
        cfg = self.settings.get();cfg.update(interval=1)
        self.assertEqual(self.post('config', cfg).status_code, 200)
        self.assertEqual(Settings(self.directory).get(), cfg)
        for name in ('config.json', 'auth.json'):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob('.*json-*')), [])
        self.assertEqual(self.client.post('/api/config', data=' ' * 40000, headers={'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'}).status_code, 413)

    def test_legacy_token_is_ignored_without_changing_display_settings(self):
        legacy = dict(self.cfg, token='legacy-pairing-value', enabled=True,
                      transport='udp', display_ip='127.0.0.1')
        atomic_json(self.directory / 'config.json', legacy)
        expected = {k: v for k, v in legacy.items() if k != 'token'}
        loaded = Settings(self.directory)
        self.assertEqual(loaded.get(), expected)
        self.assertIn('token', json.loads((self.directory / 'config.json').read_text()))
        self.login()
        for ignored in ('', 'not-a-token', None, 123):
            with self.subTest(token=ignored):
                self.assertEqual(self.post('config', dict(legacy, token=ignored)).status_code, 200)
                self.assertEqual(self.client.get('/api/config').json, expected)
                self.assertEqual(Settings(self.directory).get(), expected)
                self.assertNotIn('token', json.loads((self.directory / 'config.json').read_text()))
        self.assertIn('token', legacy)  # Normalization never mutates the caller.
        self.assertEqual(self.post('config', dict(expected, unknown=True)).status_code, 400)

    def test_failed_save_keeps_old_config(self):
        cfg = self.settings.get();cfg['interval'] = 1
        with patch('core.os.replace', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.settings.save(cfg)
        self.assertEqual(Settings(self.directory).get(), self.cfg)
        self.assertEqual(self.settings.get(), self.cfg)
        self.assertEqual(list(self.directory.glob('.*json-*')), [])

    def test_setup_import_and_refuses_overwrite(self):
        import cli
        imported = self.directory / 'old.json'
        old = {k: v for k, v in self.cfg.items() if k not in ('enabled', 'transport', 'usb_port')}
        old['display_ip'] = '10.10.161.33';old['token'] = 'd' * 32
        atomic_json(imported, old)
        destination = self.directory / 'fresh'
        args = ['cli.py', '--state-dir', str(destination), 'setup', '--import-config', str(imported)]
        with patch.object(sys, 'argv', args), patch('cli.getpass.getpass', return_value='12345678'):
            self.assertEqual(cli.main(), 0)
            before = (destination / 'config.json').read_bytes()
            self.assertEqual(cli.main(), 1)
            self.assertEqual(before, (destination / 'config.json').read_bytes())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        cfg = Settings(destination).get()
        self.assertNotIn('token', cfg)
        self.assertEqual(cfg['transport'], 'udp')
        self.assertFalse(cfg['enabled'])

    def test_real_udp_then_pause_and_resume(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(('127.0.0.1', 0));listener.settimeout(4)
            cfg = self.settings.get();cfg.update(enabled=True, display_ip='127.0.0.1', transport='udp', interval=.2, port=listener.getsockname()[1])
            self.settings.save(cfg)
            self.monitor.start()
            data, _ = listener.recvfrom(2048)
            wire = json.loads(data)
            self.assertEqual(wire['v'], 1);self.assertNotIn('token', wire)
            self.assertLess(len(data), 1024)
            self.assertNotIn('token', self.monitor.status()['metrics'])
            self.assertFalse(self.monitor.status()['receiver_confirmed'])
            self.login()
            self.assertEqual(self.post('enabled', {'enabled': False}).status_code, 200)
            count = self.monitor.status()['sent']; time.sleep(.35)
            self.assertEqual(count, self.monitor.status()['sent'])
            self.assertIsNotNone(self.monitor.status()['sample'])
            self.assertEqual(self.post('enabled', {'enabled': True}).status_code, 200)
            deadline = time.monotonic() + 3
            while self.monitor.status()['sent'] == count and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertGreater(self.monitor.status()['sent'], count)

    def test_udp_history_does_not_count_as_usb_confirmation(self):
        cfg = self.settings.get();cfg.update(enabled=True, transport='usb')
        self.settings.save(cfg)
        self.monitor.last_sent = time.time()
        self.assertFalse(self.monitor.status()['receiver_confirmed'])
        self.monitor.last_usb_ack = time.time()
        self.assertTrue(self.monitor.status()['receiver_confirmed'])
        self.login()
        self.post('enabled', {'enabled': False})
        self.post('enabled', {'enabled': True})
        self.assertFalse(self.monitor.status()['receiver_confirmed'])

    def test_transport_failure_keeps_sampling_and_recovers(self):
        cfg = self.settings.get();cfg.update(interval=.2, enabled=True, transport='usb')
        self.settings.save(cfg)
        calls = []
        def failing_link(payload, port, cancelled=None):
            calls.append(time.monotonic())
            if len(calls) <= 2:
                raise ValueError('USB test unavailable')
        with patch.object(self.monitor.usb, 'send', side_effect=failing_link):
            self.monitor.start()
            deadline = time.monotonic() + 3
            while len(calls) < 3 and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertGreaterEqual(len(calls), 3)
            self.assertLess(calls[1] - calls[0], .35)
            self.assertTrue(self.monitor.status()['receiver_confirmed'])
            self.assertIsNone(self.monitor.status()['error'])

    def test_sampling_recovers_after_error(self):
        cfg = self.settings.get();cfg['interval'] = .2;self.settings.save(cfg)
        original = core.collect.snapshot
        calls = 0
        def snapshot():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('simulated unreadable proc')
            return original()
        with patch.object(core.collect, 'snapshot', side_effect=snapshot):
            self.monitor.start()
            deadline = time.monotonic() + 3
            while self.monitor.status()['sample'] is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertIsNotNone(self.monitor.status()['sample'])
            self.assertIsNone(self.monitor.status()['error'])

    def test_cpu_source_persistence_web_wire_and_legacy_config(self):
        old = self.settings.get();old.pop('cpu_sensor')
        self.assertEqual(validate_config(old)['cpu_sensor'],'auto')
        for bad in ('/sys/class/hwmon/hwmon3/temp4_input', None, [], True):
            with self.assertRaises(ValueError): validate_config(dict(old,cpu_sensor=bad))
        chosen = 'cpu-'+'a'*24
        self.monitor.sample = {'cpu_temp_c':66,'cpu_used_percent':2,
            'cpu_temperature_sources':[{'driver':'k10temp','label':'Tctl','value_c':66}],
            'cpu_temperature_candidates':[{'id':chosen,'driver':'k10temp','label':'Tccd2','value_c':42}],
            'memory':{'used_percent':20},'nvme':[],'network':{},'gpu':[]}
        self.login()
        cfg=self.settings.get();cfg['cpu_sensor']=chosen
        self.assertEqual(self.post('config',cfg).status_code,200)
        self.assertEqual(Settings(self.directory).get()['cpu_sensor'],chosen)
        result=self.client.get('/api/status').json
        self.assertEqual(result['metrics']['ct'],42)
        self.assertEqual(result['cpu_temperature']['label'],'Tccd2')
        self.assertTrue(any('k10temp / Tccd2' in s for s in result['sensor_notes']))
        self.monitor.sample['cpu_temperature_candidates']=[]
        result=self.client.get('/api/status').json
        self.assertIsNone(result['metrics']['ct'])
        self.assertEqual(result['cpu_temperature']['status'],'missing')

    def test_n100_temperatures_and_replaced_gpu_reach_web_api(self):
        cfg = self.settings.get();cfg['gpu_device'] = '0000:08:00.0';self.settings.save(cfg)
        self.monitor.sample = {'cpu_temp_c':43, 'cpu_tctl_c':None, 'cpu_used_percent':2,
            'cpu_temperature_sources':[{'driver':'coretemp','label':'Package id 0','value_c':43}],
            'memory':{'used_percent':20}, 'nvme':[], 'network':{},
            'gpu':[{'device':'0000:00:02.0','card':'card1','vendor':'Intel','driver':'i915',
                    'temperature_c':43,'temperature_source':'i915 / temp1_input','temperature_status':'ok','busy_percent':None}]}
        self.monitor.sample_at = time.monotonic()
        self.login()
        response = self.client.get('/api/status')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['metrics']['ct'],43)
        self.assertEqual(response.json['metrics']['gt'],43)
        self.assertEqual(response.json['selected_gpu']['vendor'],'Intel')
        self.assertTrue(any('旧 GPU 地址' in s for s in response.json['sensor_notes']))
        self.assertIsNone(response.json['metrics']['gpu'])
        self.assertNotIn('AMD 显卡',self.client.get('/').text)
        gpu = self.monitor.sample['gpu'][0]
        gpu.update(busy_source='i915 PMU', busy_status='ok', busy_percent=81,
                   engine_busy_percent={'rcs0':0, 'vcs0':81})
        response = self.client.get('/api/status').json
        self.assertEqual(response['metrics']['gpu'],81)
        self.assertEqual(response['selected_gpu']['engine_busy_percent']['vcs0'],81)
        self.assertTrue(any('各引擎最高值' in s for s in response['sensor_notes']))
        for status, hint in [('permission_denied','CAP_PERFMON'), ('warming_up','等待'),
                             ('not_exposed','未提供'), ('unavailable','读取失败')]:
            gpu.update(busy_percent=None, busy_status=status)
            response = self.client.get('/api/status').json
            self.assertIsNone(response['metrics']['gpu'])
            self.assertEqual(response['metrics']['gt'],43)
            self.assertTrue(any(hint in s for s in response['sensor_notes']))

    def test_password_form_and_backend_accept_eight_characters(self):
        from core import check_password
        self.assertTrue(check_password('12345678', password_record('12345678')))
        with self.assertRaises(ValueError):
            password_record('1234567')
        html = self.client.get('/').text
        self.assertEqual(html.count('minlength="8"'), 2)
        self.assertNotIn('minlength="12"', html)

    def test_disconnected_display_keeps_live_web_metrics(self):
        cfg = self.settings.get(); cfg.update(interval=.2, enabled=True, transport='usb')
        self.settings.save(cfg)
        self.login()
        with patch.object(self.monitor.usb, 'send', side_effect=ValueError('USB 未连接')):
            self.monitor.start()
            deadline = time.monotonic() + 3
            while not self.monitor.status()['display_error'] and time.monotonic() < deadline:
                time.sleep(.02)
            a = self.client.get('/api/status').json
            self.assertEqual(a['display_error'], 'USB 未连接')
            self.assertIsNone(a['sampling_error'])
            self.assertIsNotNone(a['metrics']['cpu'])
            before = self.monitor.sample_at
            time.sleep(.4)
            self.assertGreater(self.monitor.sample_at, before)
            self.assertEqual(self.post('enabled', {'enabled': False}).status_code, 200)
            before = self.monitor.sample_at
            time.sleep(.4)
            b = self.client.get('/api/status').json
            self.assertGreater(self.monitor.sample_at, before)
            self.assertIsNone(b['display_error'])
            self.assertIsNone(b['sampling_error'])
            self.assertIsNotNone(b['metrics']['cpu'])
            self.assertEqual(b['sent'], 0)

    def test_blocked_usb_does_not_block_sampling_web_or_pause(self):
        from usb_link import TransferCancelled
        cfg = self.settings.get();cfg.update(interval=.2, enabled=True, transport='usb')
        self.settings.save(cfg)
        self.login()
        entered = threading.Event()
        ended = threading.Event()
        def pending(payload, port, cancelled):
            entered.set()
            deadline = time.monotonic() + 4
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(.02)
            ended.set()
            if cancelled():
                raise TransferCancelled()
        with patch.object(self.monitor.usb, 'send', side_effect=pending):
            self.monitor.start()
            self.assertTrue(entered.wait(2))
            before = self.monitor.sample_at
            time.sleep(.5)
            self.assertGreater(self.monitor.sample_at, before)
            start = time.monotonic()
            self.assertEqual(self.client.get('/api/status').status_code, 200)
            self.assertEqual(self.post('enabled', {'enabled': False}).status_code, 200)
            self.assertLess(time.monotonic() - start, .5)
            self.assertTrue(ended.wait(.5))
            self.assertEqual(self.monitor.sent, 0)
            self.assertFalse(self.monitor.status()['receiver_confirmed'])



class UsbTests(unittest.TestCase):
    def test_real_pty_handshake_ack_log_noise_and_reconnect(self):
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        received = []
        stop = threading.Event()
        def device():
            buf = bytearray()
            import select
            while not stop.is_set():
                if not select.select([master], [], [], .05)[0]:
                    continue
                try:
                    chunk = os.read(master, 512)
                except OSError:
                    continue
                for value in chunk:
                    if value != 10:
                        buf.append(value);continue
                    line = bytes(buf);buf.clear()
                    if line == b'NAS_DISPLAY_HELLO':
                        os.write(master, b'boot log\n{"kind":"nas-display-ready","v":1}\n')
                    elif line.startswith(b'{'):
                        data = json.loads(line);received.append(data)
                        ack = {'kind': 'nas-display-ack', 'v': 1, 'seq': data['seq']}
                        os.write(master, b'heartbeat log\n' + json.dumps(ack).encode() + b'\n')
        thread = threading.Thread(target=device, daemon=True);thread.start()
        link = UsbLink()
        link.STARTUP_WAIT = .05
        try:
            with patch('usb_link.usb_devices', return_value=[{'path': path, 'label': 'test'}]):
                payload = b'{"v":1,"token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","cpu":3}'
                link.send(payload, 'auto')
                self.assertNotIn('token', received[0])
                self.assertEqual(received[0]['kind'], 'nas-display-sample')
                link.close()
                link.send(payload, 'auto')
                self.assertNotEqual(received[0]['seq'], received[1]['seq'])
        finally:
            link.close();stop.set();thread.join(1);os.close(master);os.close(slave)

    def test_replug_replaces_descriptor_at_same_stable_path(self):
        import select
        resources = []
        received = []
        def attach():
            master, slave = pty.openpty()
            stopped = threading.Event()
            def firmware():
                buf = bytearray()
                while not stopped.is_set():
                    if not select.select([master], [], [], .02)[0]:
                        continue
                    try:
                        chunk = os.read(master, 512)
                    except OSError:
                        continue
                    for value in chunk:
                        if value != 10:
                            buf.append(value);continue
                        line = bytes(buf);buf.clear()
                        if line == b'NAS_DISPLAY_HELLO':
                            os.write(master, b'{"kind":"nas-display-ready","v":1}\n')
                        elif line.startswith(b'{'):
                            data = json.loads(line);received.append(data)
                            os.write(master, json.dumps({'kind': 'nas-display-ack', 'v': 1, 'seq': data['seq']}).encode() + b'\n')
            worker = threading.Thread(target=firmware, daemon=True);worker.start()
            resources.append((master, slave, stopped, worker))
            return os.ttyname(slave)
        link = UsbLink();link.STARTUP_WAIT = .05
        try:
            with tempfile.TemporaryDirectory() as tmp:
                stable = Path(tmp) / 'same-usb-by-id'
                stable.symlink_to(attach())
                devices = [{'path': str(stable)}]
                with patch('usb_link.usb_devices', side_effect=lambda: devices), patch('usb_link.UsbLink.STARTUP_WAIT', .05):
                    packet = b'{"v":1,"token":"private","cpu":3}'
                    link.send(packet, str(stable))
                    old = link.serial;identity = link.identity
                    self.assertTrue(old.dtr)
                    self.assertFalse(old.rts)
                    # A quick unplug/replug can finish between two sampling ticks.
                    replacement = attach()
                    stable.unlink();stable.symlink_to(replacement)
                    link.send(packet, str(stable))
                    self.assertFalse(old.is_open)
                    self.assertNotEqual(link.identity, identity)
                    self.assertEqual(len(received), 2)
                    old = link.serial
                    master, slave, stopped, worker = resources.pop()
                    stopped.set();worker.join(1);os.close(master);os.close(slave)
                    stable.unlink();devices.clear()
                    with self.assertRaises(ValueError):
                        link.send(packet, str(stable))
                    self.assertFalse(old.is_open)
                    self.assertIsNone(link.serial)
                    # Device returns under the same configured stable path.
                    stable.symlink_to(attach());devices.append({'path': str(stable)})
                    link.next_attempt = 0
                    link.send(packet, str(stable))
                    self.assertEqual(len(received), 3)
                    self.assertEqual([d['seq'] for d in received], [1, 2, 3])
                    self.assertTrue(all('token' not in d for d in received))
        finally:
            link.close()
            for master, slave, stopped, worker in resources:
                stopped.set();worker.join(1);os.close(master);os.close(slave)

    def test_failed_cleanup_forgets_old_port_and_flushes_before_close(self):
        calls = []
        class Broken:
            def reset_output_buffer(self):
                calls.append('flush')
                raise termios.error(5, 'Input/output error')
            def close(self):
                calls.append('close')
                raise termios.error(5, 'Input/output error')
        link = UsbLink();link.serial = Broken();link.path = '/dev/ttyACM0';link.identity = (1,2,3)
        link.buffer.extend(b'incomplete')
        link.close()
        self.assertEqual(calls, ['flush', 'close'])
        self.assertIsNone(link.serial)
        self.assertIsNone(link.path)
        self.assertIsNone(link.identity)
        self.assertEqual(link.buffer, b'')

    def test_write_timeout_closes_and_retries_with_fresh_connection(self):
        from unittest.mock import Mock
        import serial
        link = UsbLink();port = Mock()
        port.write.side_effect = serial.SerialTimeoutException()
        def opened(selected, cancelled):
            link.serial = port
        with patch.object(link, 'open', side_effect=opened):
            with self.assertRaisesRegex(ValueError, '写入超时'):
                link.send(b'{"v":1,"cpu":1}', 'auto')
        port.reset_output_buffer.assert_called_once()
        port.close.assert_called_once()
        self.assertIsNone(link.serial)
        self.assertGreater(link.next_attempt, time.monotonic())

    def test_auto_does_not_select_ambiguous_or_arbitrary_port(self):
        link = UsbLink()
        with patch('usb_link.usb_devices', return_value=[]):
            with self.assertRaises(ValueError):
                link.open('auto')
            with self.assertRaises(ValueError):
                link.open('/etc/passwd')
        with patch('usb_link.usb_devices', return_value=[{'path': '/dev/ttyACM0'}, {'path': '/dev/ttyACM1'}]):
            with self.assertRaises(ValueError):
                link.open('auto')

    def test_waits_full_grace_even_after_stale_ready_line(self):
        link = UsbLink()
        times = []
        class BootingSerial:
            def read(self, size):
                times.append(time.monotonic())
                time.sleep(.01)
                return b'boot log\n{"kind":"nas-display-ready","v":1}\n'
        link.serial = BootingSerial()
        link.STARTUP_WAIT = .1
        start = time.monotonic()
        link.wait_for_startup()
        self.assertGreaterEqual(time.monotonic() - start, .1)
        self.assertGreater(len(times), 3)

    def test_retry_backoff_is_not_extended_by_frequent_sampling(self):
        link = UsbLink()
        with patch('usb_link.time.monotonic', return_value=10), patch.object(link, 'open', side_effect=ValueError('not attached')) as opened:
            with self.assertRaises(ValueError):
                link.send(b'{}', 'auto')
            self.assertEqual(link.next_attempt, 12)
            with self.assertRaises(ValueError):
                link.send(b'{}', 'auto')
            self.assertEqual(link.next_attempt, 12)
            self.assertEqual(opened.call_count, 1)
        with patch('usb_link.time.monotonic', return_value=12), patch.object(link, 'open', side_effect=ValueError('not attached')) as opened:
            with self.assertRaises(ValueError):
                link.send(b'{}', 'auto')
            opened.assert_called_once()

    def test_latest_sample_is_read_after_handshake(self):
        from unittest.mock import Mock
        link = UsbLink();current = {'cpu': 1}
        port = Mock();port.write.side_effect = len
        def open_device(selected, cancelled):
            current['cpu'] = 99
            link.serial = port
        with patch.object(link, 'open', side_effect=open_device), patch.object(link, 'response'):
            link.send(lambda: json.dumps(dict(v=1, **current)).encode(), 'auto')
        self.assertEqual(json.loads(port.write.call_args.args[0])['cpu'], 99)
        link.close()

    def test_ack_must_match_sequence(self):
        link = UsbLink()
        class Fake:
            def read(self, count):
                time.sleep(.01)
                return b'{"kind":"nas-display-ack","v":1,"seq":2}\n'
        link.serial = Fake()
        with self.assertRaises(ValueError):
            link.response('nas-display-ack', .05, 1)


if __name__ == '__main__':
    unittest.main()
