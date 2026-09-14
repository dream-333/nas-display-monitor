"""Curve scheduler tests with deterministic clocks and no hardware writes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import socket
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/host'))
import fan_curve as curve
import fan_client
from test_fan_modes import fan


class Broker:
    def __init__(self):
        self.rows={ch:{'channel':ch,'mode':'auto','pwm':None,'rpm':1200,'alarm':0,'can_set':True,'can_auto':True}
                   for ch in ('pwm2','pwm3')}
        self.writes=[];self.fail=False;self.error=None
    def status(self): return {'available':True,'channels':copy.deepcopy(list(self.rows.values())),'error':self.error}
    def apply(self,ch,mode,pwm):
        if self.fail: raise ValueError('fixture failure')
        self.writes.append((ch,mode,pwm));self.rows[ch].update(mode=mode,pwm=pwm)
        return self.status()
    def recover(self):
        for ch in self.rows:self.apply(ch,'auto',None)


class Curves(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'curves.json';self.now=0.;self.temp=40.
        self.b=Broker();self.reader=lambda:[{'id':'cpu:auto','kind':'cpu','label':'CPU','temperature_c':self.temp}]
        self.e=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        self.c=curve.preset();self.c.update(step_up=0,step_down=0,hysteresis=0)
    def tick(self,temp,seconds=2):
        self.temp=temp;self.now+=seconds;self.e.tick()

    def test_presets_interpolation_floor_and_critical(self):
        for name in curve.PRESETS:
            for kind in ('cpu','board'):
                c=curve.validate(curve.preset(name,kind=kind))
                values=[curve.target(c,t) for t in range(126)]
                self.assertEqual(values,sorted(values));self.assertGreaterEqual(min(values),40)
                self.assertEqual(values[-1],100)
        c=copy.deepcopy(self.c);c['points']=[[30,20],[70,100]];c['min_percent']=40
        self.assertEqual(curve.target(c,0),40)
        self.assertEqual(curve.target(c,50),60)
        c['interpolation']='step';self.assertEqual(curve.target(c,50),40)
        self.assertEqual(curve.target(c,70),100)
        c['critical_temp']=45;self.assertEqual(curve.target(c,45),100)

    def test_reject_invalid_schema_and_nonmonotonic_or_nonfinite_points(self):
        for changes in [{'source':'/sys/evil'}, {'source':[]}, {'points':[]}, {'points':[[30,50],[20,100]]},
                        {'points':[[30,70],[60,50]]},{'points':[[30,False],[70,100]]},
                        {'points':[[float('nan'),40],[70,100]]},{'points':[[20,40]]*9},
                        {'min_percent':-1},{'min_percent':101},{'min_percent':True},{'step_up':float('inf')},
                        {'hysteresis':-1},{'critical_temp':101},{'interpolation':'evil'}, {'preset':{}},
                        {'path':'/sys/evil'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):curve.validate(dict(self.c,**changes))
        with self.assertRaises(ValueError):self.e.start('/sys/pwm1',self.c)
        self.assertEqual(self.b.writes,[])

    def test_stop_restores_baseline_start_resumes_enabled_curve(self):
        self.e.start('pwm2',self.c);self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.tick(65);self.assertEqual(self.b.rows['pwm2']['pwm'],191)
        result=fan_client.display_status(self.e.status())['channels'][0]
        self.assertEqual(result['mode'],'curve');self.assertEqual(result['speed_percent'],75)
        self.assertNotIn('pwm',result)
        self.e.recover();self.assertEqual(self.b.rows['pwm2']['mode'],'auto')
        writes=list(self.b.writes)
        resumed=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        resumed.tick();self.assertEqual(self.b.writes,writes)
        self.assertEqual(resumed.profiles['pwm2'],self.c);self.assertFalse(resumed.active)
        self.temp=70;resumed.resume()
        self.assertIn('pwm2',resumed.active)
        self.assertEqual(self.b.rows['pwm2']['mode'],'manual')
        self.assertEqual(self.b.rows['pwm2']['pwm'],(curve.target(self.c,70)*255+50)//100)
        self.assertTrue(resumed.status()['channels'][0]['curve_enabled'])

    def test_old_draft_migration_never_assumes_enabled(self):
        self.path.write_text(json.dumps({'pwm2':self.c}))
        resumed=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        resumed.resume();self.assertFalse(self.b.writes)
        resumed.start('pwm2',self.c)
        data=json.loads(self.path.read_text());self.assertEqual(data['version'],2)
        self.assertIn('pwm2',data['enabled'])

    def test_restart_after_crash_resumes_and_explicit_auto_cancels(self):
        self.e.start('pwm2',self.c)
        # Process dies without Engine.recover. Entrypoint recovers old journals first.
        self.b.recover()
        resumed=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        resumed.resume();self.assertIn('pwm2',resumed.active)
        resumed.apply('pwm2','auto',None);resumed.recover()
        restarted=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        self.b.writes.clear();restarted.resume()
        self.assertFalse(self.b.writes);self.assertFalse(restarted.enabled)

    def test_resume_missing_or_changed_source_keeps_baseline(self):
        for change in ('missing','identity'):
            with self.subTest(change=change):
                self.e.reader=lambda:[{'id':'cpu:auto','temperature_c':40,'identity':['Tctl']}]
                self.e.start('pwm2',self.c);self.e.recover()
                reader=lambda:[] if change=='missing' else [{'id':'cpu:auto','temperature_c':40,'identity':['Tccd2']}]
                resumed=curve.Engine(self.b,self.path,fan.atomic,reader,lambda:self.now)
                self.b.writes.clear();resumed.resume()
                self.assertFalse(resumed.active);self.assertFalse(self.b.writes)
                self.assertFalse(resumed.enabled);self.assertIn('自动恢复失败',resumed.errors['pwm2'])

    def test_resume_failure_does_not_block_other_channels(self):
        self.e.start('pwm2',self.c);self.e.start('pwm3',self.c);self.e.recover()
        del self.b.rows['pwm2']
        resumed=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        resumed.resume();self.assertIn('pwm3',resumed.active);self.assertNotIn('pwm2',resumed.enabled)

    def test_profile_write_failure_prevents_control(self):
        self.e.atomic=Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):self.e.start('pwm2',self.c)
        self.assertEqual(self.b.writes,[]);self.assertFalse(self.e.active)

    def test_manual_and_auto_cancel_scheduler(self):
        self.e.start('pwm2',self.c);self.e.apply('pwm2','manual',130)
        self.tick(99);self.assertEqual(self.b.rows['pwm2']['pwm'],130)
        self.e.apply('pwm2','auto',None);self.assertFalse(self.e.active)
        self.assertFalse(json.loads(self.path.read_text())['enabled'])

    def test_missing_sensor_restores_auto_and_latches_until_apply(self):
        self.e.start('pwm2',self.c);self.tick(None)
        self.assertEqual(self.b.rows['pwm2']['mode'],'auto');self.assertFalse(self.e.active)
        self.assertIn('已恢复主板自动',self.e.errors['pwm2'])
        self.assertFalse(json.loads(self.path.read_text())['enabled'])
        self.tick(60);self.assertEqual(self.b.rows['pwm2']['mode'],'auto')
        self.e.start('pwm2',self.c);self.assertNotIn('pwm2',self.e.errors)

    def test_auto_sensor_identity_change_fails_closed(self):
        identity=['Tctl']
        self.e.reader=lambda:[{'id':'cpu:auto','temperature_c':40,'identity':identity}]
        self.e.start('pwm2',self.c);identity[:]=['Tccd2'];self.tick(42)
        self.assertFalse(self.e.active);self.assertEqual(self.b.rows['pwm2']['mode'],'auto')

    def test_sensor_failure_and_restore_failure_remain_visible(self):
        self.e.start('pwm2',self.c);self.b.fail=True;self.tick(None)
        self.assertIn('未完成',self.e.status()['error']);self.assertFalse(self.e.active)

    def test_delays_hysteresis_and_critical_bypass(self):
        self.c.update(step_up=4,step_down=10,hysteresis=3)
        self.e.start('pwm2',self.c);baseline=self.b.rows['pwm2']['pwm']
        self.tick(60);self.assertEqual(self.b.rows['pwm2']['pwm'],baseline)
        self.tick(61);self.assertEqual(self.b.rows['pwm2']['pwm'],baseline)
        self.tick(60);self.assertGreater(self.b.rows['pwm2']['pwm'],baseline)
        high=self.b.rows['pwm2']['pwm'];self.tick(59,10);self.assertEqual(self.b.rows['pwm2']['pwm'],high)
        self.tick(40);self.assertEqual(self.b.rows['pwm2']['pwm'],high)
        self.tick(40,10);self.assertLess(self.b.rows['pwm2']['pwm'],high)
        self.tick(90);self.assertEqual(self.b.rows['pwm2']['pwm'],255)
        self.assertEqual(self.e.active['pwm2']['state'],'高温全速保护')

    def test_zero_rpm_grace_and_alarm_full_speed(self):
        self.e.start('pwm2',self.c);self.b.rows['pwm2']['rpm']=0
        self.tick(40);self.assertLess(self.b.rows['pwm2']['pwm'],255)
        self.tick(40,10);self.assertEqual(self.b.rows['pwm2']['pwm'],255)
        self.b.rows['pwm2'].update(rpm=1200,alarm=1);self.tick(40)
        self.assertEqual(self.b.rows['pwm2']['pwm'],255)

    def test_zero_output_is_normal_stop_and_rise_has_no_kick(self):
        self.c.update(min_percent=0,points=[[40,0],[60,50],[80,100]])
        self.e.start('pwm2',self.c);self.b.rows['pwm2'].update(rpm=0,alarm=1)
        self.assertEqual(self.b.rows['pwm2']['pwm'],0)
        for _ in range(10):self.tick(40,10)
        self.assertEqual(self.b.rows['pwm2']['pwm'],0)
        self.assertEqual(self.e.active['pwm2']['state'],'曲线停转（0%）')
        self.tick(50);self.assertEqual(self.b.rows['pwm2']['pwm'],64)
        self.tick(50,2);self.assertEqual(self.b.rows['pwm2']['pwm'],64)
        self.b.rows['pwm2'].update(rpm=700,alarm=0)
        self.tick(50,12);self.assertEqual(self.b.rows['pwm2']['pwm'],64)
        self.tick(40);self.assertEqual(self.b.rows['pwm2']['pwm'],0)
        self.b.rows['pwm2'].update(rpm=0,alarm=1)
        self.tick(90);self.assertEqual(self.b.rows['pwm2']['pwm'],255)

    def test_zero_output_resumes_after_service_restart(self):
        self.c.update(min_percent=0,points=[[40,0],[60,50],[80,100]])
        self.e.start('pwm2',self.c);self.e.recover()
        resumed=curve.Engine(self.b,self.path,fan.atomic,self.reader,lambda:self.now)
        resumed.resume();self.assertIn('pwm2',resumed.active)
        self.assertEqual(self.b.rows['pwm2']['pwm'],0)

    def test_nonzero_restart_with_no_rpm_still_detects_fault(self):
        self.c.update(min_percent=0,points=[[40,0],[60,50],[80,100]])
        self.e.start('pwm2',self.c);self.b.rows['pwm2'].update(rpm=0,alarm=1)
        self.tick(50);self.assertEqual(self.b.rows['pwm2']['pwm'],64)
        self.tick(50);self.tick(50,10)
        self.assertEqual(self.b.rows['pwm2']['pwm'],255)

    def test_external_control_is_not_overwritten(self):
        self.e.start('pwm2',self.c);self.b.rows['pwm2']['pwm']=10;self.b.writes.clear()
        self.tick(60);self.assertFalse(self.b.writes);self.assertFalse(self.e.active)
        self.assertIn('其他程序',self.e.errors['pwm2'])
        self.assertFalse(json.loads(self.path.read_text())['enabled'])

    def test_independent_channels_and_fair_tick(self):
        self.e.start('pwm2',self.c);self.e.start('pwm3',curve.preset('performance'))
        self.now=4;self.temp=90;self.e.tick();self.e.tick()
        self.assertTrue(all(r['pwm']==255 for r in self.b.rows.values()))
        self.e.apply('pwm2','auto',None);self.assertIn('pwm3',self.e.active)

    def test_requires_recoverable_hardware_and_initial_sensor(self):
        self.b.rows['pwm2']['can_auto']=False
        with self.assertRaises(ValueError):self.e.start('pwm2',self.c)
        self.b.rows['pwm2']['can_auto']=True;self.temp=None
        with self.assertRaises(ValueError):self.e.start('pwm2',self.c)
        self.assertFalse(self.b.writes)

    def test_bad_saved_profile_and_write_failures(self):
        self.path.write_text('{"/sys/evil":{}}')
        with self.assertRaises(ValueError):curve.Engine(self.b,self.path,fan.atomic)
        self.path.unlink();self.b.fail=True
        with self.assertRaises(ValueError):self.e.start('pwm2',self.c)
        self.assertFalse(self.e.active)
        self.assertFalse(json.loads(self.path.read_text())['enabled'])

    def test_reject_corrupt_enabled_state(self):
        for enabled in ({'pwm3':'cpu:auto'}, {'pwm2':None}, {'pwm2':[]}, {'pwm2':[{}]}, []):
            self.path.write_text(json.dumps({'version':2,'profiles':{'pwm2':self.c},'enabled':enabled}))
            with self.assertRaises(ValueError):curve.Engine(self.b,self.path,fan.atomic)

    def test_broker_protocol_and_notifications(self):
        data={'action':'curve','channel':'pwm2','curve':self.c}
        self.assertEqual(fan.validate_request(data),data)
        with self.assertRaises(ValueError):fan.validate_request(dict(data,temperature=1))
        path=str(self.path.with_name('notify.sock'))
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as receiver:
            receiver.bind(path);receiver.settimeout(1)
            with patch.dict('os.environ',{'NOTIFY_SOCKET':path}):
                fan.notify('READY=1');self.assertEqual(receiver.recv(128),b'READY=1')
                fan.notify('WATCHDOG=1');self.assertEqual(receiver.recv(128),b'WATCHDOG=1')
