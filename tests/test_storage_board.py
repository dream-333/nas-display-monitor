import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/collector'))
sys.path.insert(0,str(ROOT/'src/host'))
import storage, board, smart_cache, send, core

class StorageBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.block=self.root/'block';self.block.mkdir();self.hw=self.root/'hw';self.hw.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def disk(self,name,serial):
        b=self.block/name;(b/'device').mkdir(parents=True)
        for n,v in {'model':'SAME MODEL','serial':serial,'vendor':'ATA'}.items():(b/'device'/n).write_text(v)
        (b/'size').write_text('1024');return b
    def chip(self,name,driver,**attrs):
        h=self.hw/name;h.mkdir();(h/'name').write_text(driver)
        for k,v in attrs.items():(h/k).write_text(str(v))
        return h
    def test_duplicate_models_partitions_and_renumbered_disks(self):
        a=self.disk('sda','one');self.disk('sdb','two');p=self.disk('sda1','part');(p/'partition').touch()
        before=storage.inventory(self.block);self.assertEqual(len(before),2)
        self.assertNotEqual(before[0]['id'],before[1]['id'])
        a.rename(self.block/'sdc');after=storage.inventory(self.block)
        self.assertEqual(before[0]['id'],next(d['id'] for d in after if d['device']=='sdc'))
    def test_private_serial_does_not_change_identity_with_root(self):
        a=self.disk('sda','one');(a/'device/serial').chmod(0o600)
        identity=storage.inventory(self.block)[0]['id'];(a/'device/serial').write_text('two')
        self.assertEqual(identity,storage.inventory(self.block)[0]['id'])
    def test_cache_stale_sleep_missing_and_no_old_device_data(self):
        self.disk('sda','one');d=storage.inventory(self.block)[0];cache=self.root/'cache'
        def sample(entry):
            cache.write_text(json.dumps({'disks':[dict(id=d['id'],**entry)]}))
            return storage.disk_stats(self.block,self.hw,cache,now=1000)[0]
        self.assertEqual(sample({'updated_at':990,'status':'ok','temperature_c':41})['temperature_c'],41)
        self.assertIsNone(sample({'updated_at':700,'status':'ok','temperature_c':41})['temperature_c'])
        self.assertEqual(sample({'updated_at':990,'status':'sleeping','temperature_c':41})['status'],'sleeping')
        self.assertIsNone(sample({'updated_at':990,'status':'sleeping','temperature_c':41})['temperature_c'])
        (self.block/'sda/device/serial').write_text('replacement')
        self.assertEqual(storage.disk_stats(self.block,self.hw,cache,now=1000)[0]['status'],'pending')
    def test_native_nvme_multiple_namespaces_no_partition_duplicate(self):
        ctrl=self.root/'controller';ctrl.mkdir();(ctrl/'model').write_text('NVMe test');(ctrl/'serial').write_text('unique')
        h=ctrl/'hwmon7';h.mkdir();(h/'name').write_text('nvme');(h/'temp1_label').write_text('Composite');(h/'temp1_input').write_text('42850')
        (self.hw/'hwmon0').symlink_to(h)
        for n in (1,2):
            b=ctrl/f'nvme4n{n}';b.mkdir();(b/'nsid').write_text(str(n));(self.block/b.name).symlink_to(b)
        ds=storage.disk_stats(self.block,self.hw,self.root/'missing')
        self.assertEqual([d['temperature_c'] for d in ds],[42.9,42.9]);self.assertNotEqual(ds[0]['id'],ds[1]['id'])
    def test_smart_health_warning_keeps_valid_temperature(self):
        self.assertEqual(smart_cache.parse_smart({'temperature':{'current':39}},8),(39,'ok'))
        for code,status in [(3,'sleeping'),(5,'power_unknown'),(2,'unavailable')]:
            self.assertEqual(smart_cache.parse_smart({'temperature':{'current':39}},code),(None,status))
        self.assertIsNone(smart_cache.parse_smart({'temperature':{'current':255}},0)[0])
    def test_smart_fixed_read_only_command_and_timeout(self):
        def run(cmd,**kwargs):
            self.assertEqual(cmd,['/usr/sbin/smartctl','-j','-A','-d','ata','-n','standby,3,5','/dev/sda'])
            self.assertEqual(kwargs['timeout'],8)
            return SimpleNamespace(returncode=0,stdout='{"temperature":{"current":38}}')
        self.assertEqual(smart_cache.query({'kind':'SATA','device':'sda'},'/usr/sbin/smartctl',run),(38,'ok'))
    def test_sys_selection_never_guesses_it8613_temp2_or_uses_negative(self):
        self.chip('hwmon5','it8613',temp1_input=71000,temp2_input=48000,temp3_input=-33000)
        sensors=board.board_stats(self.hw);sample={'board_temperatures':sensors}
        self.assertEqual(board.select_system(sample)['status'],'select_source')
        self.assertEqual(board.select_system(sample,sensors[1]['id'])['temperature_c'],48)
        self.assertIsNone(sensors[2]['temperature_c'])
        self.assertEqual(board.select_system(sample,'missing')['status'],'missing')
    def test_sys_stable_hwmon_link_and_label_auto_fault(self):
        dev=self.root/'device';(dev/'hwmon/hwmon1').mkdir(parents=True);h=dev/'hwmon/hwmon1'
        (h/'name').write_text('nct6775');(h/'temp1_label').write_text('SYSTIN');(h/'temp1_input').write_text('39000')
        link=self.hw/'hwmon1';link.symlink_to(h);a=board.board_stats(self.hw)
        link.rename(self.hw/'hwmon9');b=board.board_stats(self.hw);self.assertEqual(a[0]['id'],b[0]['id'])
        self.assertEqual(board.select_system({'board_temperatures':b})['temperature_c'],39)
        (h/'temp1_fault').write_text('1');self.assertIsNone(board.board_stats(self.hw)[0]['temperature_c'])
    def test_config_migration_and_path_injection(self):
        cfg=core.default_config();cfg.pop('sys_sensor');self.assertEqual(core.validate_config(cfg)['sys_sensor'],'auto')
        cfg['sys_sensor']='/sys/class/hwmon/hwmon5/temp2_input'
        with self.assertRaises(ValueError):core.validate_config(cfg)
    def test_screen_rotates_all_disks_bounded_packet_and_ambiguous_legacy(self):
        ds=[{'id':f'disk-{i:024x}','device':f'sd{chr(97+i)}','kind':'SATA','model':'SAME','temperature_c':30+i,'status':'ok'} for i in range(17)]
        sample={'disks':ds,'nvme':[], 'gpu':[], 'network':{},'memory':{'used_percent':20},'cpu_used_percent':1}
        cfg={'interface':'eth0','token':'a'*32,'disks':['SAME',ds[1]['id']]}
        seen=[]
        for i in range(5):
            with patch.object(send.time,'monotonic',return_value=i*10):data=send.packet(sample,cfg)
            self.assertLess(len(data),850);data=json.loads(data);seen.extend(d['n'] for d in data['ds'])
            self.assertIsNone(data['d1']);self.assertEqual(data['d2'],31)
        self.assertEqual(len(set(seen)),17)
