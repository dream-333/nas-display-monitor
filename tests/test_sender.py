import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/collector'))
import collect
import send


class SenderTests(unittest.TestCase):
    def test_packet_maps_models_not_nvme_numbers(self):
        sample = {'cpu_used_percent': 4.5, 'cpu_tctl_c': 54, 'memory': {'used_percent': 25},
                  'gpu': [{'device': '0000:08:00.0', 'edge_c': 51, 'busy_percent': 97}],
                  'nvme': [{'device': 'nvme9', 'model': 'WD_BLACK SN770 500GB', 'composite_c': 46},
                           {'device': 'nvme8', 'model': 'Fanxiang S500Pro 1TB', 'composite_c': 41}],
                  'network': {'enp6s0': {'rx_bytes_per_sec': 5120, 'tx_bytes_per_sec': 100}}}
        cfg = {'interface': 'enp6s0', 'token': 'a'*32, 'gpu_device': 'auto', 'disks': ['Fanxiang S500Pro 1TB', 'WD_BLACK SN770 500GB']}
        wire = send.packet(sample, cfg)
        d = json.loads(wire)
        self.assertLess(len(wire), 1024)
        self.assertEqual((d['d1'], d['d2'], d['gpu']), (41, 46, 97))
        self.assertNotIn('memory_busy_percent', d)
        self.assertNotIn('token', d)
        without_token = {k: v for k, v in cfg.items() if k != 'token'}
        self.assertEqual(send.packet(sample, without_token), wire)
        sample['gpu'] = []
        sample['nvme'] = []
        sample['network'] = {}
        d = json.loads(send.packet(sample, cfg))
        self.assertIsNone(d['gt'])
        self.assertIsNone(d['d1'])
        self.assertIsNone(d['rx'])

    def test_counter_reset_and_new_interface(self):
        a = (1, (100, 60), {'eth0': (1000, 2000)})
        b = (3, (200, 80), {'eth0': (1400, 2200), 'new0': (10, 20)})
        with patch.object(collect, 'memory', return_value={}), patch.object(collect, 'temperatures', return_value={}), patch.object(collect, 'gpu_stats', return_value=[]):
            d = collect.report(a, b, None)
            self.assertEqual(d['cpu_used_percent'], 80)
            self.assertEqual(d['network']['eth0']['rx_bytes_per_sec'], 200)
            self.assertIsNone(d['network']['new0']['rx_bytes_per_sec'])
            d = collect.report(b, (5, (300, 100), {'eth0': (0, 0)}), None)
            self.assertIsNone(d['network']['eth0']['tx_bytes_per_sec'])

    def test_gpu_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            dev = r / 'devices' / '0000:08:00.0'
            dev.mkdir(parents=True)
            (dev/'driver').symlink_to(r/'amdgpu')
            drm=r/'drm';drm.mkdir()
            for name in ['card0','card1','card0-HDMI-A-1']:
                (drm/name).mkdir();(drm/name/'device').symlink_to(dev)
            (dev/'gpu_busy_percent').write_text('97')
            h=dev/'hwmon'/'hwmon9';h.mkdir(parents=True)
            (h/'name').write_text('amdgpu');(h/'temp1_label').write_text('edge');(h/'temp1_input').write_text('53000')
            g=collect.gpu_stats(drm, nvidia_reader=lambda: [])
            self.assertEqual(len(g),1)
            self.assertEqual(g[0]['edge_c'],53)
            self.assertEqual(g[0]['busy_percent'],97)
            self.assertNotIn('memory_busy_percent',g[0])
            (dev/'gpu_busy_percent').write_text('0');self.assertEqual(collect.gpu_stats(drm, nvidia_reader=lambda: [])[0]['busy_percent'],0)
            (h/'temp1_input').unlink();self.assertIsNone(collect.gpu_stats(drm, nvidia_reader=lambda: [])[0]['edge_c'])

    def test_real_udp_sender(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1',0));server.settimeout(10)
            cfg=Path(tmp)/'config.json'
            subprocess.run([sys.executable,str(ROOT/'src/collector/send.py'),'--init','--config',str(cfg)],check=True,capture_output=True)
            self.assertEqual(cfg.stat().st_mode & 0o777,0o600)
            d=json.loads(cfg.read_text());self.assertNotIn('token', d);d.update(display_ip='127.0.0.1',port=server.getsockname()[1],interface=next(iter(collect.network())),interval=.2)
            cfg.write_text(json.dumps(d))
            with subprocess.Popen([sys.executable,str(ROOT/'src/collector/send.py'),'--config',str(cfg),'--count','1']) as proc:
                wire,_=server.recvfrom(2048)
                self.assertEqual(proc.wait(timeout=10),0)
            self.assertNotIn('token', json.loads(wire))
            self.assertLess(len(wire),1024)
            d['token'] = 'ignored-legacy-code'
            cfg.write_text(json.dumps(d))
            with subprocess.Popen([sys.executable,str(ROOT/'src/collector/send.py'),'--config',str(cfg),'--count','1']) as proc:
                legacy_wire,_=server.recvfrom(2048)
                self.assertEqual(proc.wait(timeout=10),0)
            self.assertNotIn('token', json.loads(legacy_wire))
            result=subprocess.run([sys.executable,str(ROOT/'src/collector/send.py'),'--init','--config',str(cfg)],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(json.loads(cfg.read_text()),d)

if __name__=='__main__':unittest.main()
