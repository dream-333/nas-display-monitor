"""Exercise fnOS configuration and unit rendering without any privileged actions."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock, call
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
spec = importlib.util.spec_from_file_location('fpk_lifecycle', ROOT / 'src/host/fnos/lifecycle.py')
fpk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpk)
from core import check_password


class FnosTests(unittest.TestCase):
    def test_start_queues_hardware_and_fan_together_without_blocking_web(self):
        with tempfile.TemporaryDirectory() as temp:
            var=Path(temp);state=var/'state';state.mkdir()
            for name in ('auth.json','config.json'):(state/name).write_text('{}')
            with patch.dict('os.environ',TRIM_APPNAME=fpk.APP,TRIM_APPDEST=str(var/'target'),TRIM_PKGVAR=str(var)), \
                 patch.object(fpk.os,'geteuid',return_value=0), \
                 patch.object(fpk,'systemctl',return_value=0) as ctl, \
                 patch.object(fpk.pwd,'getpwnam',return_value=Mock(pw_uid=1000,pw_gid=1000)), \
                 patch.object(fpk.os,'chown'),patch.object(Path,'write_text'), \
                 patch.object(Path,'chmod'),patch('time.sleep'):
                self.assertEqual(fpk.main('start'),0)
            self.assertIn(call('start',fpk.WEB,fpk.TIMER),ctl.call_args_list)
            self.assertIn(call('start','--no-block',fpk.HARDWARE,fpk.FAN),ctl.call_args_list)
            self.assertLess(ctl.call_args_list.index(call('start',fpk.WEB,fpk.TIMER)),
                            ctl.call_args_list.index(call('start','--no-block',fpk.HARDWARE,fpk.FAN)))

    def test_initial_password_minimum_and_confirm(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            for password, confirm in [('1234567', '1234567'), ('12345678', 'different')]:
                with self.assertRaises(ValueError):
                    fpk.initialize(state, password, confirm)
                self.assertFalse(state.exists())
            self.assertTrue(fpk.initialize(state, '12345678', '12345678'))
            self.assertTrue(check_password('12345678', json.loads((state / 'auth.json').read_text())))
            self.assertFalse(json.loads((state / 'config.json').read_text())['enabled'])
            self.assertEqual((state / 'auth.json').stat().st_mode & 0o777, 0o600)

    def test_upgrade_preserves_credentials_and_display_config(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            fpk.initialize(state, '12345678', '12345678')
            old = {p.name: p.read_bytes() for p in state.iterdir()}
            self.assertFalse(fpk.initialize(state, '', ''))
            fpk.reset_password(state, '', '')
            self.assertEqual(old, {p.name: p.read_bytes() for p in state.iterdir()})
            fpk.reset_password(state, 'newpassword', 'newpassword')
            auth = json.loads((state / 'auth.json').read_text())
            self.assertTrue(check_password('newpassword', auth))
            self.assertEqual(auth['secret'], json.loads(old['auth.json'])['secret'])
            self.assertNotEqual(auth['generation'], json.loads(old['auth.json'])['generation'])
            self.assertEqual((state / 'config.json').read_bytes(), old['config.json'])

    def test_partial_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            (state / 'config.json').write_text('user data')
            with self.assertRaises(ValueError): fpk.initialize(state, '12345678', '12345678')
            self.assertEqual((state / 'config.json').read_text(), 'user data')

    def test_units_keep_web_unprivileged_and_smart_isolated(self):
        units = fpk.unit_files('/vol1/@appcenter/nas-display-fnos', '/vol1/@appdata/nas-display-fnos/state')
        web, smart = units[fpk.WEB], units[fpk.SMART]
        self.assertIn('User=nas-display-fnos', web)
        self.assertIn('CapabilityBoundingSet=CAP_PERFMON', web)
        self.assertIn('serve --port 8787', web)
        self.assertIn(' -I ', smart)
        self.assertIn('RestrictAddressFamilies=AF_UNIX', smart)
        self.assertNotIn('fan_service', ''.join(units.values()))
        self.assertNotIn('CAP_SYS_ADMIN', ''.join(units.values()))
        self.assertNotIn('nas-display-host.service', ''.join(units.values()))
        self.assertIn('fpk_hardware.py', units[fpk.HARDWARE])
        self.assertIn('User=root', units[fpk.HARDWARE])
        self.assertNotIn('fpk_hardware.py', web)
        self.assertIn('NAS_DISPLAY_HARDWARE_STATUS=', web)
        self.assertIn('NAS_DISPLAY_FAN_SOCKET=', web)
        self.assertIn(' -I ', units[fpk.FAN])
        self.assertIn('User=root', units[fpk.FAN])
        self.assertIn('RestrictAddressFamilies=AF_UNIX', units[fpk.FAN])
        self.assertIn('StateDirectoryMode=0700', units[fpk.FAN])
        self.assertIn('Restart=on-failure', units[fpk.FAN])
        self.assertIn('After=' + fpk.HARDWARE + '\n', units[fpk.FAN])
        self.assertNotIn('fpk_fan.py', web)
        with self.assertRaises(ValueError): fpk.quote('bad\nExecStart=evil')
        self.assertEqual(fpk.quote('/a b/%x'), '"/a b/%%x"')

    def test_driver_policy_is_persistent_and_not_arbitrary_command(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'policy.json'
            fpk.save_policy(None, path)
            self.assertEqual(json.loads(path.read_text()), {'policy': 'auto'})
            fpk.save_policy('existing', path); fpk.save_policy(None, path)
            self.assertEqual(json.loads(path.read_text()), {'policy': 'existing'})
            with self.assertRaises(ValueError): fpk.save_policy('it87; reboot', path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
