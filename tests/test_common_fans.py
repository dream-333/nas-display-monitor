"""Driver contracts and recovery, using temporary fake sysfs only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
import fan_hwmon as common
import fan_client
spec = importlib.util.spec_from_file_location('common_legacy', ROOT/'src/host/fnos/fan_control.py')
legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)


class FixtureHardware(common.Hardware):
    def __init__(self, root):
        super().__init__(root, 'boot-one')
        self.writes = []; self.before_write = lambda *args: None; self.fail = None

    def write(self, channel, field, value, identity):
        self.before_write(channel, field, value)
        if self.fail and self.fail(channel, field, value): raise OSError('fixture write failure')
        d = self.read(channel)
        if identity != d['identity']: raise ValueError('identity changed')
        self.writes.append((channel, field, value))
        name = d['node'] if field == 'pwm' else d['node']+'_'+field
        (d['path']/name).write_text(str(value))
        if d['alias'] is not None and field in ('pwm', 'auto_start'):
            (d['path']/d['node']).write_text(str(value))
            (d['path']/(d['node']+'_auto_start')).write_text(str(value))
            if field == 'pwm': (d['path']/(d['node']+'_enable')).write_text('0' if value == 255 else '1')


class CommonFans(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.sys = self.root/'sys'; self.sys.mkdir()
        (self.sys/'class/hwmon').mkdir(parents=True)
        self.hw = FixtureHardware(self.sys); self.path = self.root/'active.json'
        self.ctrl = common.Controller(self.hw, self.path, legacy.atomic, lambda _: None)

    def chip(self, driver='nct6798', number=1, mode=5, pwm=100):
        h = self.sys/f'devices/platform/chip{number}/hwmon/hwmon{number}'
        h.mkdir(parents=True)
        (self.sys/f'class/hwmon/hwmon{number}').symlink_to(h)
        values = {'name':driver,'pwm1':pwm,'pwm1_enable':mode,'pwm1_mode':0,
                  'pwm1_auto_point1_temp':40000,'pwm1_auto_point2_temp':70000,
                  'fan1_input':1234,'fan1_label':'CPU Fan','fan1_alarm':0}
        for name, value in values.items(): (h/name).write_text(str(value))
        ch = next(c for c, d in self.hw.discover().items() if d['path'] == h)
        return h, ch

    def test_nuvoton_keeps_original_smart_fan_mode_and_dc_type(self):
        h,ch = self.chip()
        def before(*args): self.assertEqual(json.loads(self.path.read_text())['entries'][ch]['mode'],5)
        self.hw.before_write=before
        self.ctrl.apply(ch,'manual',147)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.ctrl.apply(ch,'manual',180); self.ctrl.apply(ch,'auto',None)
        self.assertEqual((h/'pwm1_enable').read_text(),'5')
        self.assertEqual((h/'pwm1_mode').read_text(),'0')
        self.assertFalse(self.ctrl.entries)
        self.assertTrue(all(f in ('enable','pwm') for _,f,_ in self.hw.writes))
        self.assertNotIn((ch,'enable',2),self.hw.writes)

    def test_curve_engine_uses_real_generic_snapshot_and_recovery(self):
        import fan_curve
        from types import SimpleNamespace
        h,ch=self.chip('nct6799',mode=5,pwm=100)
        old=SimpleNamespace(path=self.root/'legacy.json',error=None,
                            status=lambda:{'available':False,'channels':[]},recover=lambda:None)
        broker=common.Broker(old,legacy.atomic);broker.common=self.ctrl
        now=[0];temp=[40]
        engine=fan_curve.Engine(broker,self.root/'curves.json',legacy.atomic,
            lambda:[{'id':'cpu:auto','temperature_c':temp[0]}],lambda:now[0])
        engine.start(ch,fan_curve.preset())
        self.assertEqual((h/'pwm1_enable').read_text(),'1')
        temp[0]=95;now[0]=3;engine.tick()
        self.assertEqual((h/'pwm1').read_text(),'255')
        engine.recover()
        self.assertEqual((h/'pwm1_enable').read_text(),'5')
        self.assertEqual((h/'pwm1_auto_point1_temp').read_text(),'40000')
        self.assertFalse(self.ctrl.entries)

        resumed=fan_curve.Engine(broker,self.root/'curves.json',legacy.atomic,
            lambda:[{'id':'cpu:auto','temperature_c':temp[0]}],lambda:now[0])
        resumed.resume()
        self.assertEqual((h/'pwm1_enable').read_text(),'1')
        self.assertEqual((h/'pwm1').read_text(),'255')
        resumed.recover()
        self.assertEqual((h/'pwm1_enable').read_text(),'5')
        self.assertEqual((h/'pwm1_auto_point1_temp').read_text(),'40000')

    def test_winbond_auto_thermal_mode_restored(self):
        h,ch=self.chip('w83627ehf',mode=2)
        self.ctrl.apply(ch,'manual',90);self.ctrl.apply(ch,'auto',None)
        self.assertEqual((h/'pwm1_enable').read_text(),'2')

    def test_speed_cruise_target_changes_block_restore(self):
        h,ch=self.chip(mode=3)
        (h/'fan1_target').write_text('1500')
        (h/'fan1_tolerance').write_text('100')
        self.ctrl.apply(ch,'manual',140)
        (h/'fan1_target').write_text('1700')
        self.hw.writes.clear();self.ctrl.recover()
        self.assertFalse(self.hw.writes);self.assertIn(ch,self.ctrl.errors)

    def test_ite_shared_start_and_full_duty_mode_zero(self):
        h,ch=self.chip('it8689',mode=2,pwm=70)
        (h/'pwm1_auto_start').write_text('70')
        self.ctrl.apply(ch,'manual',255)
        self.assertEqual((h/'pwm1_enable').read_text(),'0')
        self.ctrl.apply(ch,'auto',None)
        self.assertEqual((h/'pwm1_enable').read_text(),'2')
        self.assertEqual((h/'pwm1_auto_start').read_text(),'70')
        self.assertNotIn((ch,'enable',0),self.hw.writes)

    def test_manual_baseline_restores_on_stop_without_inventing_auto(self):
        h,ch=self.chip(mode=1,pwm=83)
        self.ctrl.apply(ch,'manual',120)
        self.assertFalse(self.ctrl.status()[0]['can_auto'])
        with self.assertRaises(ValueError): self.ctrl.apply(ch,'auto',None)
        self.ctrl.recover()
        self.assertEqual((h/'pwm1').read_text(),'83')
        self.assertEqual((h/'pwm1_enable').read_text(),'1')

    def test_multiple_chips_same_channel_never_cross_write(self):
        h,a=self.chip(number=1);k,b=self.chip(number=2)
        self.assertNotEqual(a,b);self.ctrl.apply(a,'manual',200)
        self.assertEqual((k/'pwm1_enable').read_text(),'5')
        self.assertEqual((k/'pwm1').read_text(),'100')
        recovered=common.Controller(self.hw,self.path,legacy.atomic,lambda _:None)
        recovered.recover();self.assertEqual((h/'pwm1_enable').read_text(),'5')

    def test_hwmon_renumber_is_stable_but_reloaded_device_is_not(self):
        h,ch=self.chip();self.ctrl.apply(ch,'manual',120)
        link=self.sys/'class/hwmon/hwmon1';link.rename(link.with_name('hwmon88'))
        self.assertIn(ch,self.hw.discover())
        self.ctrl.entries[ch]['identity']['inode']+=1;self.hw.writes.clear();self.ctrl.recover()
        self.assertTrue(self.ctrl.errors);self.assertFalse(self.hw.writes)

    def test_changed_curve_blocks_restore_and_keeps_record(self):
        h,ch=self.chip();self.ctrl.apply(ch,'manual',120)
        (h/'pwm1_auto_point2_temp').write_text('71000')
        self.hw.writes.clear();self.ctrl.recover()
        self.assertIn(ch,self.ctrl.entries);self.assertFalse(self.hw.writes)

    def test_disk_failure_prevents_any_write(self):
        _,ch=self.chip()
        with patch.object(self.ctrl,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.ctrl.apply(ch,'manual',120)
        self.assertFalse(self.hw.writes);self.assertFalse(self.ctrl.entries)

    def test_failed_pwm_restores_original_and_failed_restore_keeps_journal(self):
        h,ch=self.chip();self.hw.fail=lambda c,f,v:f=='pwm'
        with self.assertRaisesRegex(ValueError,'已恢复'):self.ctrl.apply(ch,'manual',120)
        self.assertEqual((h/'pwm1_enable').read_text(),'5')
        self.hw.fail=None;self.ctrl.apply(ch,'manual',140)
        self.hw.fail=lambda c,f,v:f=='enable' and v==5
        with self.assertRaises(OSError):self.ctrl.apply(ch,'auto',None)
        self.assertIn(ch,json.loads(self.path.read_text())['entries'])

    def test_new_boot_discards_journal_without_touching_hardware(self):
        _,ch=self.chip();self.ctrl.apply(ch,'manual',120);self.hw.writes.clear()
        self.hw.boot='next-boot';c=common.Controller(self.hw,self.path,legacy.atomic,lambda _:None);c.recover()
        self.assertFalse(self.hw.writes);self.assertFalse(c.entries)

    def test_unknown_driver_fault_missing_nodes_do_not_hide_good_chip(self):
        h,ch=self.chip('not-supported');k,other=self.chip(number=2)
        self.assertFalse(next(d for d in self.ctrl.status() if d['channel']==ch)['can_set'])
        with self.assertRaises(ValueError):self.ctrl.apply(ch,'manual',100)
        (h/'pwm1_enable').unlink();(k/'fan1_fault').write_text('1')
        rows=self.ctrl.status();self.assertEqual(len(rows),2)
        self.assertTrue(next(d for d in rows if d['channel']==other)['can_set'])
        self.assertIsNone(next(d for d in rows if d['channel']==other)['rpm'])
        (k/'fan1_fault').unlink();(k/'fan1_input').write_text('0')
        self.assertEqual(self.hw.read(other)['rpm'],0)

    def test_nofollow_id_validation_readonly_and_snapshot_corruption(self):
        h,ch=self.chip();real=common.Hardware(self.sys,'boot-one')
        for value in ['../pwm1','hwmon-ffffffffffffffff-pwm1',None,[]]:
            with self.assertRaises(ValueError):real.read(value)
        identity=real.read(ch)['identity'];(h/'fan1_input').write_text('100')
        (h/'pwm1').unlink();(h/'pwm1').symlink_to(h/'fan1_input')
        with self.assertRaises(OSError):real.write(ch,'pwm',120,identity)
        self.assertEqual((h/'fan1_input').read_text(),'100')
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):common.Controller(self.hw,self.path,legacy.atomic)

    def test_api_validation_accepts_discovery_ids_not_arbitrary_paths(self):
        _,ch=self.chip()
        self.assertTrue(fan_client.valid_channel(ch))
        self.assertEqual(legacy.validate_request(dict(action='set',channel=ch,mode='manual',pwm=128))['channel'],ch)
        for c in ['pwm1','../../pwm1','hwmon-abc-pwm1',None,{}]:self.assertFalse(fan_client.valid_channel(c))
