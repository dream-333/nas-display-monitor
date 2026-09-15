"""Driver preparation must refuse unsafe loading and never unload host modules."""
import errno
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('docker_driver', ROOT / 'src/host/docker/driver_init.py')
driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / 'sys'; self.proc = self.root / 'proc'; self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.put(self.sys/'class/dmi/id/board_vendor', 'Centerm')
        self.put(self.sys/'class/dmi/id/board_name', 'Zero1 pro')
        self.put(self.proc/'sys/kernel/modules_disabled', '0')
        self.put(self.proc/'self/status', 'CapEff:\t0000000000010000\n')
        modules = {}
        for name in ('hwmon-vid.ko', 'it87.ko'):
            (self.bundle/name).write_bytes(name.encode()); modules[name] = hashlib.sha256(name.encode()).hexdigest()
        (self.bundle/'MODULES.json').write_text(json.dumps({'kernel': driver.KERNEL, 'driver_version': driver.VERSION, 'modules': modules}))
        self.calls = []

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)

    def load(self, path):
        self.calls.append(path.name)
        if path.name == 'it87.ko':
            self.put(self.sys/'class/hwmon/hwmon0/name', 'it8613')
            self.put(self.sys/'module/it87/version', driver.VERSION)

    def prepare(self, **kw):
        args = dict(bundle=self.bundle, sys=self.sys, proc=self.proc, kernel=driver.KERNEL,
                    arch='x86_64', loader=self.load, sleep=lambda _: None)
        return driver.prepare(**dict(args, **kw))

    def test_load_dependencies_then_matching_driver(self):
        self.assertEqual(self.prepare()['state'], 'ready')
        self.assertEqual(self.calls, ['hwmon-vid.ko', 'it87.ko'])
        self.assertEqual(self.prepare()['state'], 'existing_only')
        self.assertEqual(len(self.calls), 2)

    def test_wrong_kernel_arch_policy_or_board_never_loads(self):
        for kw in ({'kernel': '6.1.0-40-amd64'}, {'arch':'aarch64'}, {'policy':'existing-only'}, {'policy':'force'}):
            self.assertNotEqual(self.prepare(**kw)['state'], 'ready')
        self.put(self.sys/'class/dmi/id/board_name', 'Different board')
        self.assertEqual(self.prepare()['state'], 'unsupported')
        self.assertEqual(self.calls, [])

    def test_existing_conflicting_module_is_not_replaced(self):
        self.put(self.sys/'module/it87/version', 'other-version')
        self.assertEqual(self.prepare()['state'], 'needs_attention')
        self.assertEqual(self.calls, [])

    def test_permission_signature_and_disabled_load_guards(self):
        for path, text in [(self.proc/'sys/kernel/modules_disabled','1'),
                           (self.sys/'kernel/security/lockdown','none [integrity] confidentiality'),
                           (self.proc/'self/status','CapEff:\t0000000000000000\n')]:
            previous = path.read_text() if path.exists() else None
            self.put(path, text)
            self.assertEqual(self.prepare()['state'], 'needs_attention')
            self.assertEqual(self.calls, [])
            if previous is None: path.unlink()
            else: path.write_text(previous)

    def test_all_hashes_verified_before_any_load(self):
        (self.bundle/'it87.ko').write_bytes(b'bad')
        self.assertEqual(self.prepare()['state'], 'needs_attention')
        self.assertEqual(self.calls, [])

    def test_load_errors_do_not_claim_success(self):
        for code in (errno.EPERM, errno.ENOEXEC, errno.ENODEV, errno.EIO, 129):
            def refuse(_): raise OSError(code, 'test')
            self.assertEqual(self.prepare(loader=refuse)['state'], 'needs_attention')
        self.assertEqual(self.prepare(loader=lambda _: None)['state'], 'needs_attention')

    def test_published_report_is_readable_without_credentials(self):
        path = self.root/'report/status.json'
        value = self.prepare(kernel='unsupported')
        driver.publish(value, path)
        self.assertEqual(json.loads(path.read_text()), value)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
