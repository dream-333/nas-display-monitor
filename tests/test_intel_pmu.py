import ctypes
import errno
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/collector'))
import collect
import intel_pmu
import send


class IntelPMUTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.online = self.root / 'online'; self.online.write_text('2-7')
        self.now = 100.0
        self.values = {}
        self.opened = []
        self.closed = []
        self.monitor = intel_pmu.IntelPMU(self.root, self.online, self.open_counter,
                                        lambda fd: self.values[fd], self.closed.append,
                                        lambda: self.now)
        self.gpu = {'driver': 'i915', 'device': '0000:00:02.0'}

    def tearDown(self):
        self.monitor.close()
        self.tmp.cleanup()

    def open_counter(self, kind, config, cpu):
        fd = 100 + len(self.opened)
        self.opened.append((kind, config, cpu))
        self.values[fd] = (0, 1_000_000_000, 1_000_000_000)
        return fd

    def pmu(self, name='i915', engines=('rcs0', 'vcs0', 'vecs0', 'bcs0')):
        path = self.root / name; (path / 'events').mkdir(parents=True)
        (path / 'type').write_text('27')
        for index, engine in enumerate(engines):
            (path / 'events' / (engine + '-busy')).write_text(f'config=0x{index * 16:x}')
            (path / 'events' / (engine + '-busy.unit')).write_text('ns')
        return path

    def sample(self, devices=None):
        return self.monitor.sample(devices or [self.gpu])[self.gpu['device']]

    def test_video_only_load_and_idle_zero_propagate_to_screen(self):
        self.pmu()
        self.assertEqual(self.sample()['busy_status'], 'warming_up')
        self.assertEqual(self.opened, [(27, 48, 2), (27, 0, 2), (27, 16, 2), (27, 32, 2)])
        # Sorted sysfs: bcs0, rcs0, vcs0, vecs0. Only video decode is busy.
        for fd in self.values:
            self.values[fd] = (800_000_000 if fd == 102 else 0, 2_000_000_000, 2_000_000_000)
        value = self.sample()
        self.assertEqual(value['busy_percent'], 80)
        self.assertEqual(value['engine_busy_percent']['vcs0'], 80)
        self.assertEqual(value['engine_busy_percent']['rcs0'], 0)
        import json
        sample = {'gpu': [dict(self.gpu, **value)], 'cpu_used_percent': 1,
                  'memory': {'used_percent': 25}, 'nvme': [], 'network': {}}
        packet = json.loads(send.packet(sample, {'gpu_device': 'auto', 'token': 'test', 'interface': 'eth0'}))
        self.assertEqual(packet['gpu'], 80)
        for fd, (busy, enabled, running) in self.values.items():
            self.values[fd] = (busy, enabled + 1_000_000_000, running + 1_000_000_000)
        self.assertEqual(self.sample()['busy_percent'], 0)

    def test_rates_multiplexing_reset_and_clamp(self):
        base = (10, 100, 100)
        self.assertEqual(intel_pmu.percentage(base, (50, 200, 150)), 80)
        self.assertEqual(intel_pmu.percentage(base, (110, 200, 200)), 100)
        self.assertEqual(intel_pmu.percentage(base, (111, 200, 200)), 100)
        self.assertIsNone(intel_pmu.percentage(base, (9, 200, 200)))
        self.assertIsNone(intel_pmu.percentage(base, (10, 100, 100)))
        self.assertIsNone(intel_pmu.percentage(base, (10, 200, 100)))
        self.assertIsNone(intel_pmu.percentage(base, (10, 200, 250)))

    def test_multi_gpu_mapping_and_no_xe_guess(self):
        self.pmu()
        discrete = {'driver': 'i915', 'device': '0000:03:00.0'}
        self.assertEqual(self.monitor.mapping([self.gpu, discrete]), {})
        path = self.pmu('i915_0000_03_00.0')
        mapping = self.monitor.mapping([self.gpu, discrete, {'driver':'xe', 'device':'0000:04:00.0'}])
        self.assertEqual(mapping, {self.gpu['device']:self.root / 'i915', discrete['device']:path})

    def test_no_pmu_no_zero_and_removed_gpu_closes_descriptors(self):
        self.assertEqual(self.sample()['busy_status'], 'not_exposed')
        self.assertIsNone(self.sample()['busy_percent'])
        self.pmu(); self.sample()
        self.assertEqual(self.monitor.sample([]), {})
        self.assertEqual(len(self.closed), 4)

    def test_permission_failure_closes_partial_open_and_retries(self):
        self.pmu()
        opener = self.monitor.opener
        def denied(kind, config, cpu):
            if len(self.opened):
                raise PermissionError(errno.EACCES, 'Denied')
            return opener(kind, config, cpu)
        self.monitor.opener = denied
        self.assertEqual(self.sample()['busy_status'], 'permission_denied')
        self.assertEqual(self.closed, [100])
        self.monitor.opener = opener
        self.assertEqual(self.sample()['busy_status'], 'permission_denied')
        self.now += 11
        self.assertEqual(self.sample()['busy_status'], 'warming_up')

    def test_read_failure_and_counter_reset_never_retain_old_percentage(self):
        self.pmu(engines=('rcs0',)); self.sample()
        self.values[100] = (500_000_000, 2_000_000_000, 2_000_000_000)
        self.assertEqual(self.sample()['busy_percent'], 50)
        self.values[100] = (0, 1, 1)
        self.assertIsNone(self.sample()['busy_percent'])
        self.monitor.reader = Mock(side_effect=OSError(errno.EIO, 'GPU removed'))
        value = self.sample()
        self.assertIsNone(value['busy_percent'])
        self.assertEqual(value['busy_status'], 'unavailable')
        self.assertEqual(self.closed, [100])

    def test_cpumask_reprobe_type_change_and_invalid_events(self):
        path = self.pmu(engines=('rcs0',))
        (path / 'cpumask').write_text('4,6')
        (path / 'events/rcs0-wait').write_text('config=0x1')
        (path / 'events/invalid-busy').write_text('config1=42')
        (path / 'events/wrong-busy').write_text('config=123')
        (path / 'events/wrong-busy.unit').write_text('cycles')
        self.sample()
        self.assertEqual(self.opened, [(27, 0, 4)])
        (path / 'type').write_text('29')
        self.assertEqual(self.sample()['busy_status'], 'warming_up')
        self.assertEqual(self.closed, [100])
        self.assertEqual(self.opened[-1], (29, 0, 4))

    def test_empty_events_and_invalid_cpu_are_visible(self):
        path = self.pmu(engines=())
        self.assertEqual(self.sample()['busy_status'], 'not_exposed')
        self.now += 11
        (path / 'cpumask').write_text('bad')
        self.assertEqual(self.sample()['busy_status'], 'unavailable')

    def test_collector_uses_pmu_only_when_sysfs_busy_missing(self):
        drm = self.root / 'drm'; card = drm / 'card7'; card.mkdir(parents=True)
        device = self.root / '0000:00:02.0'; device.mkdir()
        (device / 'driver').symlink_to('/sys/bus/pci/drivers/i915')
        (card / 'device').symlink_to(device)
        reader = Mock(return_value={device.name: {'busy_percent': 67, 'busy_source': 'i915 PMU'}})
        result = collect.gpu_stats(drm, lambda: [], self.root / 'hwmon', reader)
        self.assertEqual(result[0]['busy_percent'], 67)
        self.assertEqual(reader.call_args[0][0][0]['device'], device.name)
        (device / 'gpu_busy_percent').write_text('0')
        result = collect.gpu_stats(drm, lambda: [], self.root / 'hwmon', reader)
        self.assertEqual(result[0]['busy_percent'], 0)
        self.assertEqual(reader.call_args[0][0], [])

    def test_native_perf_abi_and_short_read(self):
        libc = Mock()
        def syscall(number, attr, pid, cpu, group, flags):
            self.assertEqual(number.value, 298)
            self.assertEqual(struct.unpack_from('=IIQQQQQ', ctypes.string_at(attr, 64)),
                             (27, 64, 0x100, 0, 0, 3, 0))
            self.assertEqual((pid.value, cpu.value, group.value, flags.value), (-1, 2, -1, 8))
            return 7
        libc.syscall.side_effect = syscall
        with patch.object(intel_pmu.platform, 'machine', return_value='x86_64'), patch.object(intel_pmu.ctypes, 'CDLL', return_value=libc):
            self.assertEqual(intel_pmu.open_counter(27, 0x100, 2), 7)
            libc.syscall.side_effect = None; libc.syscall.return_value = -1
            with patch.object(intel_pmu.ctypes, 'get_errno', return_value=errno.EPERM):
                with self.assertRaises(PermissionError): intel_pmu.open_counter(27, 0, 2)
        with patch.object(intel_pmu.os, 'read', return_value=struct.pack('=QQQ', 7, 9, 9)):
            self.assertEqual(intel_pmu.read_counter(7), (7,9,9))
        with patch.object(intel_pmu.os, 'read', return_value=b''):
            with self.assertRaises(OSError): intel_pmu.read_counter(7)
        with patch.object(intel_pmu.platform, 'machine', return_value='unknown'):
            with self.assertRaises(OSError): intel_pmu.open_counter(27, 0, 2)


if __name__ == '__main__':
    unittest.main()
