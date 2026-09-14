import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/collector'))
import collect


class FanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def chip(self, index, driver, **files):
        h = self.root / f'hwmon{index}'; h.mkdir()
        (h / 'name').write_text(driver)
        for name, value in files.items():
            (h / name).write_text(str(value))
        return h

    def test_n100_current_chips_have_no_fan_or_pwm(self):
        for i, name in enumerate(('acpitz','nvme','nvme','i915','coretemp')):
            self.chip(i, name, temp1_input=43000)
        data = collect.fan_stats(self.root)
        self.assertEqual(data['fans'], [])
        self.assertEqual(data['fan_controls'], [])
        self.assertEqual(len(data['fan_chips']), 5)

    def test_discovery_multiple_channels_zero_and_rpm_not_pwm_percent(self):
        h = self.chip(42, 'nct6798', fan1_input=1380, fan1_label='CPU Fan',
                      fan7_input=0, fan7_alarm=1, pwm2=128, pwm2_enable=5, pwm2_mode=1)
        before = {p.name:p.read_bytes() for p in h.iterdir()}
        data = collect.fan_stats(self.root)
        self.assertEqual([f['rpm'] for f in data['fans']], [1380,0])
        self.assertEqual(data['fans'][0]['label'], 'CPU Fan')
        self.assertEqual(data['fans'][1]['label'], 'fan7')
        self.assertEqual(data['fans'][1]['status'], 'ok')
        self.assertEqual(data['fans'][1]['alarm'], 1)
        control = data['fan_controls'][0]
        self.assertEqual(control['duty_percent'], 50.2)
        self.assertEqual(control['channel'], 'pwm2')
        self.assertEqual(control['enable'], 5)
        # Discovery must never write to hardware, nor infer pwm2 = fan2.
        self.assertNotIn('fan_channel', control)
        self.assertEqual(before, {p.name:p.read_bytes() for p in h.iterdir()})

    def test_invalid_disabled_fault_and_unreadable_never_become_zero(self):
        h = self.chip(1, 'test', fan1_input='-1', fan2_input='nan', fan3_input=1500,
                      fan3_fault=1, fan4_input=1800, fan4_enable=0, fan5_input='1.5', fan6_input=1200)
        original = collect.read
        def read(path):
            if Path(path) == h / 'fan6_input': return None
            return original(path)
        with patch.object(collect, 'read', side_effect=read):
            data = collect.fan_stats(self.root)
        self.assertTrue(all(f['rpm'] is None for f in data['fans']))
        self.assertEqual([f['status'] for f in data['fans']],
                         ['unreadable','unreadable','fault','disabled','unreadable','unreadable'])
        json.dumps(data, allow_nan=False)

    def test_target_without_feedback_and_pwm_without_fan(self):
        self.chip(8, 'controller', fan3_target=2400, fan3_label='Chassis',
                  fan9_min=600, pwm7=255, pwm7_enable=1)
        data = collect.fan_stats(self.root)
        self.assertEqual(len(data['fans']), 1)
        fan = data['fans'][0]
        self.assertIsNone(fan['rpm'])
        self.assertEqual(fan['status'], 'not_exposed')
        self.assertEqual(fan['target_rpm'], 2400)
        self.assertTrue(fan['target_exposed'])
        self.assertEqual(data['fan_controls'][0]['duty_percent'], 100)

    def test_aliases_and_hot_removal(self):
        real = self.root / 'devices' / 'controller' / 'hwmon' / 'hwmon7'
        real.mkdir(parents=True); (real / 'name').write_text('it87'); (real / 'fan1_input').write_text('1250')
        (self.root / 'hwmon9').symlink_to(real)
        (self.root / 'hwmon10').symlink_to(real)
        data = collect.fan_stats(self.root)
        self.assertEqual(len(data['fans']), 1)
        self.assertEqual(data['fans'][0]['device_path'], str(self.root / 'devices/controller'))
        (real / 'fan1_input').unlink()
        self.assertEqual(collect.fan_stats(self.root)['fans'], [])

    def test_pwm_bounds_and_names(self):
        self.chip(1, 'pwm-fan', pwm1=0, pwm2=256, pwm3=-1, pwm4='nan',
                  pwm1_freq=25000, fan0_input=999, pwm0=12, pwm5_enable=1)
        data = collect.fan_stats(self.root)
        self.assertEqual(data['fans'], [])
        self.assertEqual([c['raw'] for c in data['fan_controls']], [0,None,None,None])
        self.assertEqual(data['fan_controls'][0]['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
