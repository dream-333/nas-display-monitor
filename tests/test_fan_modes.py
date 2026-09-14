"""Exercise IT8613 aliasing, journal recovery and API gates without real hardware."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
spec = importlib.util.spec_from_file_location('fan_broker', ROOT / 'src/host/fnos/fan_control.py')
fan = importlib.util.module_from_spec(spec); spec.loader.exec_module(fan)


class FakeHardware:
    def __init__(self):
        self.data = {c: {'identity': {'boot': 'boot1', 'device': '/fixture/it8613', 'inode': 45, 'driver': fan.VERSION},
                        'mode_raw': 2, 'pwm': value, 'auto_start': value,
                        'curve': dict(zip(fan.CURVE, [i, 40000, 36000, 45000, 95000, 24, 23437])),
                        'rpm': 1234, 'alarm': 0, 'temperature_c': 43}
                     for c, value, i in [('pwm2', 73, 1), ('pwm3', 106, 2)]}
        self.writes = []; self.check_write = lambda *args: None
        self.fail = None

    def read(self, channel): return copy.deepcopy(self.data[channel])

    def write(self, channel, field, value, identity):
        self.check_write(channel, field, value)
        if self.fail and self.fail(channel, field, value): raise OSError('simulated hardware failure')
        self.writes.append((channel, field, value))
        d = self.data[channel]
        if identity != d['identity']: raise ValueError('identity changed')
        if field == 'enable': d['mode_raw'] = value
        else:
            d['pwm'] = d['auto_start'] = value  # Critical shared register, not independent fields.
            if d['mode_raw'] != 2: d['mode_raw'] = 0 if value == 255 else 1


class FanModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'active.json'; self.hw = FakeHardware()
        self.c = fan.Controller(self.hw, self.path, lambda _: None, 'boot1')

    def test_journal_before_writes_and_restore_nonhardcoded_start(self):
        def before_write(channel, *args):
            entry = json.loads(self.path.read_text())['entries'][channel]
            self.assertEqual(entry['auto_start'], 73)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.hw.check_write = before_write
        original = self.hw.read('pwm2')
        self.c.apply('pwm2', 'manual', 120)
        self.c.apply('pwm2', 'manual', 160)
        self.assertEqual(self.c.entries['pwm2']['auto_start'], 73)
        self.c.apply('pwm2', 'auto', None)
        self.assertEqual(self.hw.read('pwm2'), original)
        self.assertEqual(self.hw.writes[-2:], [('pwm2', 'auto_start', 73), ('pwm2', 'enable', 2)])
        self.assertEqual(json.loads(self.path.read_text())['entries'], {})
        self.assertTrue(all(field in ('enable', 'pwm', 'auto_start') for _, field, _ in self.hw.writes))
        self.assertNotIn(('pwm2', 'enable', 0), self.hw.writes)

    def test_curve_engine_preserves_it8613_auto_start(self):
        import fan_curve
        from types import SimpleNamespace
        now=[0];temp=[40]
        broker=SimpleNamespace(status=self.c.status,apply=self.c.apply,recover=self.c.recover,error=None)
        engine=fan_curve.Engine(broker,self.path.with_name('curves.json'),fan.atomic,
            lambda:[{'id':'cpu:auto','temperature_c':temp[0]}],lambda:now[0])
        engine.start('pwm2',fan_curve.preset())
        self.assertEqual(self.hw.data['pwm2']['mode_raw'],1)
        temp[0]=99;now[0]=3;engine.tick()
        self.assertEqual(self.hw.data['pwm2']['pwm'],255)
        engine.apply('pwm2','auto',None)
        self.assertEqual(self.hw.data['pwm2']['auto_start'],73)
        self.assertEqual(self.hw.data['pwm2']['mode_raw'],2)

    def test_curve_restart_and_new_boot_keep_it8613_baseline(self):
        import fan_curve
        curve_path=self.path.with_name('curves.json')
        reader=lambda:[{'id':'cpu:auto','temperature_c':40}]
        config=fan_curve.preset();config.update(min_percent=0,points=[[40,0],[80,100]])
        engine=fan_curve.Engine(self.c,curve_path,fan.atomic,reader)
        engine.start('pwm2',config)
        self.assertEqual(self.hw.data['pwm2']['auto_start'],0)
        engine.recover()
        self.assertEqual(self.hw.data['pwm2']['auto_start'],73)
        self.assertEqual(self.hw.data['pwm2']['mode_raw'],2)
        restarted=fan.Controller(self.hw,self.path,lambda _:None,'boot1');restarted.recover()
        resumed=fan_curve.Engine(restarted,curve_path,fan.atomic,reader);resumed.resume()
        self.assertEqual(self.hw.data['pwm2']['mode_raw'],1)
        self.assertEqual(self.hw.data['pwm2']['pwm'],0)
        # Next boot: BIOS installs a new automatic baseline; old snapshots are ignored.
        self.hw.data['pwm2'].update(mode_raw=2,pwm=90,auto_start=90)
        for row in self.hw.data.values():row['identity']['boot']='boot2'
        rebooted=fan.Controller(self.hw,self.path,lambda _:None,'boot2');rebooted.recover()
        resumed=fan_curve.Engine(rebooted,curve_path,fan.atomic,reader);resumed.resume()
        self.assertEqual(self.hw.data['pwm2']['pwm'],0)
        resumed.recover()
        self.assertEqual(self.hw.data['pwm2']['auto_start'],90)
        self.assertEqual(self.hw.data['pwm2']['mode_raw'],2)

    def test_full_duty_driver_mode_zero_and_manual_zero_are_explicit(self):
        self.c.apply('pwm3', 'manual', 255)
        self.assertEqual(self.hw.data['pwm3']['mode_raw'], 0)
        self.assertEqual(self.c.status()['channels'][1]['mode'], 'manual')
        self.c.apply('pwm3', 'manual', 0)
        self.assertEqual(self.hw.data['pwm3']['pwm'], 0)
        self.c.apply('pwm3', 'auto', None)
        self.assertEqual(self.hw.data['pwm3']['auto_start'], 106)

    def test_disk_failure_before_first_write(self):
        with patch.object(self.c, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.c.apply('pwm2', 'manual', 120)
        self.assertEqual(self.hw.writes, []); self.assertEqual(self.c.entries, {})

    def test_same_boot_crash_recovers_both_channels(self):
        for c in fan.CHANNELS: self.c.apply(c, 'manual', 255)
        recovered = fan.Controller(self.hw, self.path, lambda _: None, 'boot1')
        recovered.recover()
        self.assertIsNone(recovered.error)
        self.assertEqual([self.hw.data[c]['pwm'] for c in fan.CHANNELS], [73, 106])
        self.assertTrue(all(self.hw.data[c]['mode_raw'] == 2 for c in fan.CHANNELS))

    def test_new_boot_never_restores_old_values(self):
        self.c.apply('pwm2', 'manual', 120); self.hw.writes.clear()
        recovered = fan.Controller(self.hw, self.path, lambda _: None, 'boot2'); recovered.recover()
        self.assertEqual(self.hw.writes, [])
        self.assertEqual(recovered.entries, {})
        self.assertTrue(self.path.with_name('previous-boot.json').exists())

    def test_driver_reload_and_curve_changes_block_restoration(self):
        for field in ('identity', 'curve'):
            with self.subTest(field=field):
                self.setUp(); self.c.apply('pwm2', 'manual', 120)
                data = self.hw.data['pwm2'][field]
                key = 'inode' if field == 'identity' else 'auto_point3_temp'
                data[key] += 1; self.hw.writes.clear(); self.c.recover()
                self.assertIn('pwm2', self.c.entries)
                self.assertIsNotNone(self.c.error); self.assertEqual(self.hw.writes, [])

    def test_foreign_manual_cannot_be_taken_over(self):
        self.hw.data['pwm2']['mode_raw'] = 1
        for mode, pwm in [('manual', 100), ('auto', None)]:
            with self.assertRaises(ValueError): self.c.apply('pwm2', mode, pwm)
        self.assertFalse(self.c.status()['channels'][0]['can_set']); self.assertEqual(self.hw.writes, [])

    def test_failed_manual_write_restores_original_auto(self):
        self.hw.fail = lambda c, f, v: f == 'pwm' and v == 200
        with self.assertRaisesRegex(ValueError, '已恢复'): self.c.apply('pwm2', 'manual', 200)
        self.assertEqual(self.hw.data['pwm2']['mode_raw'], 2)
        self.assertEqual(self.hw.data['pwm2']['auto_start'], 73)
        self.assertEqual(self.c.entries, {})

    def test_failed_restore_retains_journal_and_error_across_other_channel_success(self):
        self.c.apply('pwm2', 'manual', 120)
        self.hw.fail = lambda c, f, v: c == 'pwm2' and f == 'auto_start'
        with self.assertRaises(ValueError): self.c.apply('pwm2', 'auto', None)
        self.c.apply('pwm3', 'manual', 130)
        self.assertIn('pwm2', self.c.error)
        self.assertIn('pwm2', json.loads(self.path.read_text())['entries'])
        with self.assertRaises(ValueError): self.c.apply('pwm2', 'manual', 180)
        self.hw.fail = None; self.c.apply('pwm2', 'auto', None)
        self.assertIsNone(self.c.error)

    def test_failed_journal_cleanup_is_retryable(self):
        self.c.apply('pwm2', 'manual', 120)
        with patch.object(self.c, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(ValueError): self.c.apply('pwm2', 'auto', None)
        self.assertIn('pwm2', self.c.entries)
        self.c.apply('pwm2', 'auto', None)
        self.assertNotIn('pwm2', self.c.entries)

    def test_bad_readback_restores_and_does_not_report_success(self):
        original = self.hw.write
        def wrong(c, f, v, identity): original(c, f, 100 if f == 'pwm' else v, identity)
        self.hw.write = wrong
        with self.assertRaisesRegex(ValueError, '已恢复'): self.c.apply('pwm2', 'manual', 140)
        self.assertEqual(self.hw.data['pwm2']['mode_raw'], 2)

    def test_zero_rpm_alarm_is_visible_not_synthetic_success(self):
        self.hw.data['pwm3'].update(rpm=0, alarm=1)
        status = self.c.status()['channels'][1]
        self.assertEqual(status['rpm'], 0); self.assertEqual(status['alarm'], 1)
        self.assertIsNone(status['pwm'])

    def test_invalid_auto_baseline_is_not_taken_over(self):
        self.hw.data['pwm2']['curve']['auto_point3_temp'] = 30000
        with self.assertRaisesRegex(ValueError, '自动参数异常'): self.c.apply('pwm2', 'manual', 130)
        self.assertEqual(self.hw.writes, [])
        self.assertFalse(self.path.exists())

    def test_request_boundaries_and_peer_access(self):
        for data in [None, [], {}, {'action': 'stop'}, {'action': 'status', 'extra': 1}]:
            with self.assertRaises(ValueError): fan.validate_request(data)
        for value in [-1, 256, 1.2, True, None, '120']:
            with self.assertRaises(ValueError): fan.validate_request(dict(action='set', channel='pwm2', mode='manual', pwm=value))
        for ch in ['pwm4', '../pwm2', [], None]:
            with self.assertRaises(ValueError): fan.validate_request(dict(action='set', channel=ch, mode='manual', pwm=120))
        self.assertTrue(fan.allowed(0, 990)); self.assertTrue(fan.allowed(990, 990)); self.assertFalse(fan.allowed(1000, 990))

    def test_corrupt_journal_prevents_all_writes(self):
        self.path.write_text('{bad json')
        with self.assertRaises(ValueError): fan.Controller(self.hw, self.path, lambda _: None, 'boot1')
        self.assertEqual(self.hw.writes, [])

class FanClientTests(unittest.TestCase):
    def exchange(self, response):
        import os
        import socket
        import threading
        import fan_client
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'control.sock')
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path); server.listen(1)
                def serve():
                    conn, _ = server.accept()
                    with conn:
                        self.assertEqual(json.loads(conn.recv(1024)), {'action': 'status'})
                        conn.sendall(response)
                worker = threading.Thread(target=serve); worker.start()
                try:
                    with patch.dict(os.environ, NAS_DISPLAY_FAN_SOCKET=path):
                        return fan_client.request({'action': 'status'})
                finally: worker.join(timeout=2)

    def test_socket_success_and_bounded_invalid_responses(self):
        self.assertEqual(self.exchange(b'{"ok":true,"status":{"available":true}}\n'), {'available': True})
        for response in [b'{"ok":true}\n', b'not json\n', b'[]\n', b'x' * 65537 + b'\n', b'{"ok":false,"error":"restore pending"}\n']:
            with self.assertRaises(ValueError): self.exchange(response)

    def test_missing_socket_is_unavailable_not_hardware_write(self):
        import os
        import fan_client
        with patch.dict(os.environ, NAS_DISPLAY_FAN_SOCKET='/nonexistent/fan.sock'):
            self.assertFalse(fan_client.status()['available'])


class HardwareWriteTests(unittest.TestCase):
    def test_allowlist_identity_and_nofollow_at_actual_file_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Path(tmp); node = h / 'pwm2'; node.write_text('73\n')
            identity = {'inode': h.stat().st_ino}
            hw = fan.Hardware()
            with patch.object(hw, 'locate', return_value=(h, identity)):
                for ch, field, value in [('pwm4','pwm',120), ('pwm2','enable',0), ('pwm2','pwm',256), ('pwm2','../evil',120)]:
                    with self.assertRaises(ValueError): hw.write(ch, field, value, identity)
                with self.assertRaises(ValueError): hw.write('pwm2','pwm',120, {'inode':0})
                self.assertEqual(node.read_text(), '73\n')
                hw.write('pwm2','pwm',120,identity); self.assertEqual(node.read_text(), '120\n')
                node.unlink(); node.symlink_to(h/'other')
                with self.assertRaises(OSError): hw.write('pwm2','pwm',110,identity)

class FanClientDiagnosticsTests(unittest.TestCase):
    def test_transport_errors_distinguish_startup_permissions_and_uncertain_write(self):
        import os
        import fan_client
        errors = [(FileNotFoundError(), '尚未创建'), (PermissionError(), '权限'),
                  (ConnectionRefusedError(), '没有服务监听'), (TimeoutError(), '操作可能仍在进行'),
                  (ConnectionResetError(), '连接中断')]
        for failure, message in errors:
            with self.subTest(error=type(failure).__name__):
                with patch.dict(os.environ, NAS_DISPLAY_FAN_SOCKET='/fixture/control.sock'):
                    with patch('fan_client.socket.socket', side_effect=failure):
                        status = fan_client.status()
                self.assertFalse(status['available'])
                self.assertIn(message, status['error'])

class FanBrokerPermissionsTests(unittest.TestCase):
    def test_fnos_primary_appusers_group_is_not_socket_service_group(self):
        """Run startup/bind/cleanup; model fnOS's different account primary group.

        No real root, hardware read or control request is involved.
        """
        import os
        import socket
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'active.json'; path = str(Path(tmp) / 'control.sock')
            controller = Mock(); controller.error = None
            with patch.object(fan, 'STATE', state), patch.object(fan, 'SOCKET', path), \
                 patch.object(fan.os, 'geteuid', return_value=0), \
                 patch.object(fan.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=23000)), \
                 patch.object(fan.grp, 'getgrnam', return_value=SimpleNamespace(gr_gid=24000)) as group, \
                 patch.object(fan.os, 'chown') as chown, \
                 patch.object(fan.signal, 'signal'), \
                 patch.object(fan, 'Controller', return_value=controller), \
                 patch('fan_hwmon.Broker', return_value=controller), \
                 patch.object(socket.socket, 'accept', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt): fan.main()
                group.assert_called_once_with('nas-display-fnos')
                chown.assert_called_once_with(path, 0, 24000)
            self.assertEqual(controller.recover.call_count, 2)
            controller.apply.assert_not_called()
            self.assertFalse(Path(path).exists())
