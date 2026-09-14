"""Hardware fixtures; no dependency on sensors installed on the test machine."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/collector'))
import collect
import send


class SensorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hwmon = self.root / 'hwmon';self.hwmon.mkdir()
        self.thermal = self.root / 'thermal';self.thermal.mkdir()
        self.drm = self.root / 'drm';self.drm.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def hw(self, name, channels, index=7, parent=None):
        h = (parent or self.hwmon) / ('hwmon' + str(index));h.mkdir(parents=True)
        (h / 'name').write_text(name)
        for number, (label, value) in channels.items():
            if label is not None:(h / f'temp{number}_label').write_text(label)
            (h / f'temp{number}_input').write_text(str(value))
        return h

    def gpu(self, driver, address='0000:00:02.0', card='card1', channels=None):
        device = self.root / 'devices' / address;device.mkdir(parents=True)
        (device / 'driver').symlink_to(self.root / driver)
        c = self.drm / card;c.mkdir();(c / 'device').symlink_to(device)
        if channels is not None:self.hw(driver, channels, parent=device / 'hwmon')
        return device

    def temps(self):
        return collect.temperatures(self.hwmon, self.thermal)

    def gpus(self, reader=lambda: []):
        return collect.gpu_stats(self.drm, nvidia_reader=reader, hwmon_root=self.hwmon)

    def test_n100_reported_coretemp_and_i915_without_label(self):
        self.hw('acpitz', {1: (None,27800)}, 0)
        # Changed hwmon/temp indices must not affect identification by labels.
        self.hw('coretemp', {9: ('Package id 0',43000), 2: ('Core 0',41000), 3: ('Core 1',41000)}, 42)
        self.gpu('i915', channels={1: (None,43000)})
        cpu = self.temps();gpu = self.gpus()[0]
        self.assertEqual(cpu['cpu_temp_c'],43)
        self.assertIsNone(cpu['cpu_tctl_c'])
        self.assertEqual(gpu['temperature_c'],43)
        self.assertEqual(gpu['driver'],'i915')
        self.assertEqual(gpu['temperature_status'],'ok')
        self.assertIsNone(gpu['edge_c'])
        self.assertIsNone(gpu['busy_percent'])

    def test_packages_use_hottest_socket_and_fallback_to_cores(self):
        self.hw('coretemp', {1: ('Package id 0',43000), 2: ('Core 0',51000)}, 1)
        h=self.hw('coretemp', {1: ('Package id 1',60000)}, 2)
        self.assertEqual(self.temps()['cpu_temp_c'],60)
        (h/'temp1_input').write_text('N/A')
        self.assertEqual(self.temps()['cpu_temp_c'],43)
        (self.hwmon/'hwmon1/temp1_input').write_text('nan')
        self.assertEqual(self.temps()['cpu_temp_c'],51)

    def test_amd_tdie_and_legacy_tctl_remain_distinct(self):
        self.hw('k10temp',{1:('Tctl',74000),2:('Tdie',54000)})
        value=self.temps()
        self.assertEqual(value['cpu_temp_c'],54)
        self.assertEqual(value['cpu_tctl_c'],74)

    def test_cpu_selection_9950x_shared_packet_and_stable_hwmon_identity(self):
        h = self.hw('k10temp', {1:('Tctl',66625),3:('Tccd1',62000),4:('Tccd2',41875)}, 3)
        self.hw('asusec', {1:('CPU',54000),2:('CPU Package',66000),3:('Motherboard',28000)}, 4)
        self.hw('nvme', {1:('Composite',42000)}, 5)
        sample = self.temps()
        self.assertEqual(collect.select_cpu_temperature(sample)['value_c'],66.62)
        candidates = sample['cpu_temperature_candidates']
        self.assertEqual(len(candidates),5)
        chosen = next(s['id'] for s in candidates if s['label']=='Tccd2')
        self.assertEqual(collect.select_cpu_temperature(sample,chosen)['value_c'],41.88)
        h.rename(self.hwmon/'hwmon83')
        self.assertEqual(collect.select_cpu_temperature(self.temps(),chosen)['value_c'],41.88)
        config = {'token':'a'*32,'interface':'eth0','cpu_sensor':chosen}
        sample.update(cpu_used_percent=1,memory={'used_percent':2},network={})
        self.assertEqual(json.loads(send.packet(sample,config))['ct'],41.88)
        (self.hwmon/'hwmon83/temp4_fault').write_text('1')
        self.assertEqual(collect.select_cpu_temperature(self.temps(),chosen)['status'],'unreadable')
        (self.hwmon/'hwmon83/temp4_input').unlink()
        self.assertEqual(collect.select_cpu_temperature(self.temps(),chosen)['status'],'missing')
        self.assertIsNone(collect.select_cpu_temperature(sample,'')['value_c'])

    def test_manual_cpu_does_not_fallback_on_hardware_replacement(self):
        self.hw('coretemp',{1:('Package id 0',43000)},1)
        sample=self.temps()
        self.assertEqual(collect.select_cpu_temperature(sample)['value_c'],43)
        self.assertIsNone(collect.select_cpu_temperature(sample,'cpu-'+'a'*24)['value_c'])

    def test_thermal_fallback_never_uses_acpi_or_disk(self):
        self.hw('acpitz',{1:(None,27800)})
        self.assertIsNone(self.temps()['cpu_temp_c'])
        zone=self.thermal/'thermal_zone15';zone.mkdir();(zone/'type').write_text('x86_pkg_temp');(zone/'temp').write_text('47000')
        self.assertEqual(self.temps()['cpu_temp_c'],47)
        (zone/'type').write_text('cpu-thermal');self.assertEqual(self.temps()['cpu_temp_c'],47)
        (zone/'type').write_text('acpitz');self.assertIsNone(self.temps()['cpu_temp_c'])

    def test_gpu_nested_hwmon_and_drm_connector_dedup(self):
        device=self.gpu('xe')
        h=self.hw('xe',{1:('GPU',52000),2:('VRAM',99000)},parent=device/'tile0/hwmon')
        (self.hwmon/'hwmon80').symlink_to(h)
        for card in ('card5','card1-HDMI-A-1'):
            path=self.drm/card;path.mkdir();(path/'device').symlink_to(device)
        devices=self.gpus()
        self.assertEqual(len(devices),1)
        self.assertEqual(devices[0]['temperature_c'],52)

    def test_gpu_missing_unreadable_and_zero_temperature(self):
        device=self.gpu('i915')
        self.hw('coretemp',{1:('Package id 0',43000)})
        self.assertIsNone(self.gpus()[0]['temperature_c'])
        self.assertEqual(self.gpus()[0]['temperature_status'],'not_exposed')
        h=self.hw('i915',{1:(None,'NaN')},parent=device/'hwmon')
        self.assertEqual(self.gpus()[0]['temperature_status'],'unreadable')
        (h/'temp1_input').write_text('0')
        self.assertEqual(self.gpus()[0]['temperature_c'],0)
        (h/'temp1_input').write_text('999000')
        self.assertIsNone(self.gpus()[0]['temperature_c'])
        with patch('collect.Path.read_text', side_effect=PermissionError):
            self.assertIsNone(self.gpus()[0]['temperature_c'])

    def test_amd_and_nouveau_temperature(self):
        self.gpu('amdgpu',address='0000:08:00.0',channels={1:('edge',51000),2:('junction',74000),3:('mem',95000)})
        self.gpu('nouveau',address='0000:09:00.0',card='card8',channels={1:(None,61000)})
        devices=self.gpus()
        self.assertEqual([g['temperature_c'] for g in devices],[51,61])
        self.assertEqual(devices[0]['edge_c'],51)

    def test_nvidia_smi_normalizes_bus_and_missing_data(self):
        collect._nvidia_cache=(float('-inf'),[])
        response=subprocess.CompletedProcess([],0,'00000000:0A:00.0, Example GPU, 55, 0\n00000000:0B:00.0, Other GPU, N/A, [Not Supported]\n','')
        with patch('collect.shutil.which',return_value='/usr/bin/nvidia-smi'),patch('collect.subprocess.run',return_value=response) as runner:
            values=collect.nvidia_stats()
            self.assertEqual(values[0]['device'],'0000:0a:00.0')
            self.assertEqual(values[0]['busy_percent'],0)
            self.assertIsNone(values[1]['temperature_c'])
            self.assertEqual(runner.call_args.kwargs['timeout'],1)
            self.assertNotIn('shell',runner.call_args.kwargs)
        self.gpu('nvidia',address='0000:0a:00.0')
        devices=self.gpus(lambda:values)
        self.assertEqual(len(devices),2)
        self.assertEqual(devices[0]['temperature_c'],55)
        self.assertEqual(devices[0]['temperature_source'],'nvidia-smi')
        collect._nvidia_cache=(float('-inf'),[])
        with patch('collect.shutil.which',return_value='/usr/bin/nvidia-smi'),patch('collect.subprocess.run',side_effect=subprocess.TimeoutExpired('nvidia-smi',1)):
            self.assertEqual(collect.nvidia_stats(),[])

    def test_auto_gpu_handles_hardware_change_but_not_ambiguous_manual_target(self):
        g={'device':'0000:00:02.0','temperature_c':43}
        sample={'gpu':[g]}
        self.assertEqual(send.select_gpu(sample,{'gpu_device':'0000:08:00.0'}),g)
        self.assertEqual(send.select_gpu(sample,{'gpu_device':'auto'}),g)
        self.assertEqual(send.select_gpu(sample,{'gpu_device':''}),{})
        other={'device':'0000:0a:00.0','boot_vga':True}
        sample['gpu'].append(other)
        self.assertEqual(send.select_gpu(sample,{'gpu_device':'0000:08:00.0'}),{})
        self.assertEqual(send.select_gpu(sample,{'gpu_device':'auto'}),other)

    def test_packet_generic_temperature_compatibility_and_nulls(self):
        sample={'cpu_temp_c':43,'cpu_tctl_c':None,'cpu_used_percent':1,'memory':{'used_percent':2},
                'gpu':[{'device':'0000:00:02.0','temperature_c':43,'busy_percent':None}], 'nvme':[], 'network':{}}
        config={'token':'a'*32,'interface':'eth0','gpu_device':'auto'}
        data=json.loads(send.packet(sample,config))
        self.assertEqual((data['ct'],data['gt']),(43,43))
        self.assertIsNone(data['gpu'])
        sample['gpu'][0]['temperature_c']=None
        self.assertIsNone(json.loads(send.packet(sample,config))['gt'])


if __name__=='__main__':unittest.main()
