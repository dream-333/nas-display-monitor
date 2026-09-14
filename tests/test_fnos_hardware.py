"""Driver preparation tests use fake sysfs, fake commands and temporary files only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
from hardware_status import hardware_status
spec = importlib.util.spec_from_file_location('hardware', ROOT / 'src/host/fnos/hardware.py')
hw = importlib.util.module_from_spec(spec); spec.loader.exec_module(hw)


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = '6.18.18.c1032-trim'
        self.setup = hw.Setup(kernel=self.kernel, hwmon=self.root / 'hwmon', dmi=self.root / 'dmi',
                              efi=self.root / 'efi', modules=self.root / 'modules',
                              sys_modules=self.root / 'sysmodules', runtime=self.root / 'runtime', kit=self.root / 'kit',
                              dkms=self.root / 'dkms', cmdline=self.root / 'cmdline')
        self.put('dmi/board_vendor', 'Centerm'); self.put('dmi/board_name', 'Zero1 pro')
        self.commands = []
        self.setup.command = self.command
        self.valid_headers()
        names = ['INSTALL.sh', 'SOURCE.json'] + ['source/' + n for n in
                 ('it87.c', 'compat.h', 'Makefile', 'VERSION', 'dkms.conf', 'COPYING', 'README', 'SHA256SUMS')]
        for name in names:
            p = self.setup.kit / name; p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'hardware/drivers/it87' / name, p)
        (self.setup.kit / 'KIT-SHA256.json').write_text(json.dumps({n: hashlib.sha256((self.setup.kit / n).read_bytes()).hexdigest() for n in names}))

    def put(self, name, value):
        p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(value); return p

    def valid_headers(self):
        prefix = 'modules/' + self.kernel + '/build/'
        self.put(prefix + 'Makefile', '')
        self.put(prefix + 'Module.symvers', 'symbols')
        self.put(prefix + 'include/generated/utsrelease.h', '#define UTS_RELEASE "' + self.kernel + '"')
        self.put(prefix + '.config', 'CONFIG_CC_VERSION_TEXT="x86_64-linux-gnu-gcc-14 (Debian 14)"')

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[0].endswith('dpkg-query'): return 0, 'installed'
        if args[0] == '/bin/bash' and args[-1] != '--check':
            self.put('hwmon/hwmon7/name', 'it8613')
            self.put('hwmon/hwmon7/fan2_input', '1450')
            self.put('hwmon/hwmon7/fan3_input', '0')
        return 0, ''

    def state(self):
        return json.loads((self.setup.runtime / 'status.json').read_text())['state']

    def test_existing_readable_zero_rpm_skips_every_install_command(self):
        self.put('hwmon/hwmon1/name', 'it8613'); self.put('hwmon/hwmon1/fan2_input', '0')
        self.setup.run('auto')
        self.assertEqual(self.state(), 'ready'); self.assertEqual(self.commands, [])

    def test_unknown_board_is_not_blindly_probed(self):
        self.put('dmi/board_name', 'Unknown')
        self.setup.run('auto')
        self.assertEqual(self.state(), 'unsupported'); self.assertEqual(self.commands, [])

    def test_existing_only_does_not_install(self):
        self.setup.run('existing')
        self.assertEqual(self.state(), 'existing_only'); self.assertEqual(self.commands, [])

    def test_loaded_driver_without_fan_is_not_replaced(self):
        (self.setup.sys_modules / 'it87').mkdir(parents=True)
        self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention'); self.assertEqual(self.commands, [])

    def test_secure_boot_enabled_and_unknown_block_without_changes(self):
        efivar = self.root / 'efi/efivars/SecureBoot-test'; efivar.parent.mkdir(parents=True)
        for data in (b'\0\0\0\0\1', b'bad'):
            efivar.write_bytes(data)
            self.setup.run('auto')
            self.assertEqual(self.state(), 'needs_attention'); self.assertEqual(self.commands, [])

    def test_source_tampering_stops_before_dependencies(self):
        (self.setup.kit / 'source/it87.c').write_text('altered')
        with self.assertRaises(ValueError): self.setup.run('auto')
        self.assertFalse(any('dpkg-query' in a[0] or 'apt-get' in a[0] for a in self.commands))

    def test_foreign_dkms_and_kernel_options_stop_before_packages(self):
        (self.setup.dkms / 'other-version').mkdir(parents=True)
        self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention'); self.assertEqual(self.commands, [])
        shutil.rmtree(self.setup.dkms)
        self.put('cmdline', 'quiet it87.force_id=0x8613')
        self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention'); self.assertEqual(self.commands, [])

    def test_matching_headers_preflight_then_load_then_verify(self):
        with patch.object(hw.time, 'sleep'): self.setup.run('auto')
        self.assertEqual(self.state(), 'ready')
        scripts = [a for a in self.commands if a[0] == '/bin/bash']
        self.assertEqual(scripts, [['/bin/bash', str(self.setup.kit / 'INSTALL.sh'), '--check'],
                                  ['/bin/bash', str(self.setup.kit / 'INSTALL.sh')]])
        self.assertEqual(hw.compiler_package(self.setup.headers), 'gcc-14')
        self.assertFalse(any(a[0].endswith('apt-get') for a in self.commands))
        self.assertEqual((self.setup.runtime / 'status.json').stat().st_mode & 0o777, 0o644)

    def test_preflight_failure_does_not_load_module(self):
        original = self.command
        def fail(args, **kwargs):
            if args[-1] == '--check': self.commands.append(args); return 1, 'conflict'
            return original(args, **kwargs)
        self.setup.command = fail; self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention')
        self.assertFalse(any(a[0] == '/bin/bash' and a[-1] != '--check' for a in self.commands))

    def test_missing_headers_request_exact_kernel_not_generic(self):
        shutil.rmtree(self.setup.headers)
        packages = []
        def prepare(names): packages.extend(names); self.valid_headers(); return True
        self.setup.packages = prepare
        with patch.object(hw.time, 'sleep'): self.setup.run('auto')
        self.assertEqual(packages, ['linux-headers-' + self.kernel])
        self.assertEqual(self.state(), 'ready')

    def test_wrong_headers_stop_before_build(self):
        (self.setup.headers / 'include/generated/utsrelease.h').write_text('#define UTS_RELEASE "different"')
        self.setup.packages = lambda names: True
        self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention')
        self.assertFalse(any(a[0] == '/bin/bash' for a in self.commands))

    def test_failed_repository_does_not_continue_or_remove_packages(self):
        self.setup.command = lambda args, **kwargs: (1, '')
        self.setup.run('auto')
        self.assertEqual(self.state(), 'needs_attention')
        self.commands.clear(); self.setup.command = self.command
        self.assertTrue(self.setup.packages(['gcc-14']))
        install = next(a for a in self.commands if 'install' in a)
        self.assertIn('--no-remove', install)
        self.assertNotIn('--allow-unauthenticated', install)
        with self.assertRaises(ValueError): self.setup.packages(['gcc-14;reboot'])

    def test_public_report_is_bounded_and_optional(self):
        with patch.dict(os.environ, {}, clear=True): self.assertIsNone(hardware_status())
        path = self.root / 'public.json'
        with patch.dict(os.environ, NAS_DISPLAY_HARDWARE_STATUS=str(path)):
            self.assertEqual(hardware_status()['state'], 'waiting')
            path.write_text(json.dumps({'state': 'ready', 'message': 'ready', 'kernel': self.kernel, 'private': 'never expose'}))
            self.assertNotIn('private', hardware_status())
            path.write_text('x' * 20000)
            self.assertEqual(hardware_status()['state'], 'waiting')


if __name__ == '__main__': unittest.main()
