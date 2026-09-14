"""Regression: a mode=2 readback must not reopen unsafe fan control."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src/host'), str(ROOT / 'src/collector')]
spec = importlib.util.spec_from_file_location('fan_service', ROOT / 'tests/fixtures/withdrawn_fan_service.py')
fan_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fan_service)


def load(name):
    spec = importlib.util.spec_from_file_location(name, (ROOT / 'tests/fixtures/withdrawn_fan_pulse.py' if name == 'FAN-PULSE' else ROOT / 'hardware/drivers/it87' / (name + '.py')))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FanHoldTests(unittest.TestCase):
    def test_withdrawn_pulse_cannot_touch_files(self):
        module = load('FAN-PULSE')
        with patch.object(Path, 'write_text', side_effect=AssertionError('hardware write')):
            with self.assertRaises(RuntimeError): module.pulse(Path('/not-hardware'), 'pwm3')
            with self.assertRaises(SystemExit): module.main()

    def test_draft_cannot_start_configure_or_write_any_mode(self):
        with patch.object(fan_service.os, 'open', side_effect=AssertionError('unexpected open')):
            with self.assertRaises(ValueError): fan_service.main()
            with self.assertRaises(ValueError): fan_service.configure(['pwm2:fan2'])
            hw = fan_service.Hardware({})
            for mode in (0, 1, 2):
                with self.assertRaises(ValueError): hw.write('pwm3', 'enable', mode)
            with self.assertRaises(ValueError): hw.write('pwm3', 'pwm', 200)

    def test_apply_is_disabled_even_with_good_temperature_and_mode2(self):
        profile = {'driver': 'it8613', 'device_path': '/sys/devices/platform/it87.2624',
                   'board_vendor': 'Centerm', 'board_name': 'Zero1 pro',
                   'module_version': fan_service.DRIVER_VERSION, 'mapping': {'pwm3': 'fan3'}}
        hw = Mock()
        hw.temperature.return_value = 43
        hw.read.return_value = {'mode': 2, 'raw': 100, 'rpm': 1326, 'fault': False, 'disabled': False}
        controller = fan_service.Controller(profile, hardware=hw)
        with self.assertRaises(ValueError): controller.request({'action': 'apply', 'channel': 'pwm3', 'percent': 80})
        hw.write.assert_not_called()
        self.assertFalse(controller.status()['available'])

    def test_diagnostic_keeps_zero_feedback_and_curve_and_is_read_only(self):
        module = load('FAN-CHECK')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            h = root / 'hwmon8'; h.mkdir()
            for name, value in {'name': 'it8613', 'fan3_input': '0', 'pwm3_enable': '2',
                                'pwm3': '255', 'pwm3_auto_channels_temp': '2',
                                'pwm3_auto_point1_temp': '30000', 'pwm3_auto_start': '100',
                                'temp2_input': '25000', 'serial': 'excluded'}.items():
                (h / name).write_text(value)
            before = {p.name: p.read_bytes() for p in h.iterdir()}
            with patch.object(Path, 'write_text', side_effect=AssertionError('diagnostic must not write')):
                report = module.report(root=root, system=root, proc=root, sleep=lambda _: None)
            self.assertEqual(before, {p.name: p.read_bytes() for p in h.iterdir()})
            self.assertEqual(report['initial'][0]['attributes']['pwm3_auto_start'], '100')
            self.assertNotIn('serial', report['initial'][0]['attributes'])
            self.assertEqual(len(report['samples']), 3)
            self.assertTrue(all(s['chips'][0]['attributes']['fan3_input'] == '0' for s in report['samples']))
            self.assertIsNone(report['kernel'])


if __name__ == '__main__':
    unittest.main()
