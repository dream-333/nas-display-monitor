"""Container first start and restart must never reset credentials or settings."""
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
spec = importlib.util.spec_from_file_location('docker_entrypoint', ROOT / 'src/host/docker/entrypoint.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
from core import check_password
import collect


class ContainerInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'data'
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_generated_password_persists_and_restart_preserves_legacy_config(self):
        self.assertTrue(entry.initialize(self.state))
        password = (self.state / 'initial-password.txt').read_text().strip()
        auth = json.loads((self.state / 'auth.json').read_text())
        self.assertTrue(check_password(password, auth))
        self.assertEqual((self.state / 'initial-password.txt').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / 'auth.json').stat().st_mode & 0o777, 0o600)
        cfg = json.loads((self.state / 'config.json').read_text())
        cfg.update(transport='udp', display_ip='192.0.2.10', token='old-token')
        (self.state / 'config.json').write_text(json.dumps(cfg))
        before = {n: (self.state / n).read_bytes() for n in ('auth.json', 'config.json')}
        os.environ['NAS_DISPLAY_ADMIN_PASSWORD'] = 'replacement-password'
        self.assertFalse(entry.initialize(self.state))
        self.assertEqual(before, {n: (self.state / n).read_bytes() for n in before})

    def test_password_file_and_invalid_password_do_not_create_auth(self):
        secret = Path(self.temp.name) / 'password'
        secret.write_text('secret-for-test\n')
        os.environ['NAS_DISPLAY_ADMIN_PASSWORD_FILE'] = str(secret)
        self.assertTrue(entry.initialize(self.state))
        self.assertTrue(check_password('secret-for-test', json.loads((self.state / 'auth.json').read_text())))
        self.assertFalse((self.state / 'initial-password.txt').exists())
        other = Path(self.temp.name) / 'invalid'
        secret.write_text('short')
        with self.assertRaises(ValueError): entry.initialize(other)
        self.assertFalse((other / 'auth.json').exists())
        self.assertFalse((other / 'config.json').exists())

    def test_partial_initialization_reuses_secret_and_refuses_lost_config(self):
        self.state.mkdir()
        (self.state / 'initial-password.txt').write_text('initial-secret-for-test\n')
        cfg = entry.default_config()
        cfg.update(transport='udp', display_ip='192.0.2.11')
        (self.state / 'config.json').write_text(json.dumps(cfg))
        entry.initialize(self.state)
        self.assertTrue(check_password('initial-secret-for-test', json.loads((self.state / 'auth.json').read_text())))
        self.assertEqual(json.loads((self.state / 'config.json').read_text()), cfg)
        old = (self.state / 'auth.json').read_bytes()
        (self.state / 'config.json').unlink()
        with self.assertRaises(ValueError): entry.initialize(self.state)
        self.assertEqual((self.state / 'auth.json').read_bytes(), old)

    def test_host_proc_reads_host_memory_and_network(self):
        proc = Path(self.temp.name) / 'proc'
        (proc / 'net').mkdir(parents=True)
        (proc / 'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 750 kB\n')
        (proc / 'net/dev').write_text('headers\nheaders\n eth-test: 123 0 0 0 0 0 0 0 456 0 0 0 0 0 0 0\n')
        with patch.object(collect, 'PROC_ROOT', proc):
            self.assertEqual(collect.memory()['used_percent'], 25)
            self.assertIn('eth-test', collect.network())


if __name__ == '__main__': unittest.main()
