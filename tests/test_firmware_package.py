"""Offline first-flash regressions; never enumerate or write a real serial port."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'tools/flash'))
from firmware_images import compose, validate_esp_image, validate_partitions
from package_firmware import build
import first_flash


class FirstFlashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.stage = Path(cls.temp.name)/'First-Flash'
        build(cls.stage)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_flash(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return first_flash.main(['--port', '/dev/test-only', *args], root=self.stage)

    def test_factory_contains_boot_partition_ota_app_and_erased_gaps(self):
        raw = (self.stage/'factory.bin').read_bytes()
        for offset,name in ((0,'bootloader.bin'),(0x8000,'partitions.bin'),(0xe000,'boot_app0.bin'),(0x10000,'firmware.bin')):
            image = (self.stage/name).read_bytes()
            self.assertEqual(raw[offset:offset+len(image)], image)
        self.assertEqual(raw[0x9000:0xe000], b'\xff'*0x5000)
        self.assertEqual(raw[0x10000:], (ROOT/'assets/UI5.1/firmware.bin').read_bytes())

    def test_esp_chip_and_corrupt_payload_rejected(self):
        raw = bytearray((self.stage/'bootloader.bin').read_bytes())
        raw[12] = 0
        with self.assertRaisesRegex(ValueError, 'ESP32-S3'): validate_esp_image(raw)
        raw = bytearray((self.stage/'firmware.bin').read_bytes()); raw[60] ^= 1
        with self.assertRaises(ValueError): validate_esp_image(raw)

    def test_partition_corruption_rejected(self):
        raw = bytearray((self.stage/'partitions.bin').read_bytes()); raw[4] ^= 1
        with self.assertRaises(ValueError): validate_partitions(raw)

    def test_changed_firmware_rejected_before_device_access(self):
        p=self.stage/'factory.bin';original=p.read_bytes()
        try:
            p.write_bytes(original[:-1]+bytes([original[-1]^1]))
            with mock.patch.object(first_flash.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'Checksum'):self.run_flash('--confirm-erase')
                run.assert_not_called()
        finally:p.write_bytes(original)

    def test_missing_manifest_target_rejected(self):
        p=self.stage/'FILES.json';original=p.read_bytes()
        try:
            p.write_text('[]')
            with self.assertRaisesRegex(ValueError,'Incomplete'):first_flash.verify(self.stage)
        finally:p.write_bytes(original)

    def test_dry_run_never_connects_or_requests_erase(self):
        with mock.patch.object(first_flash.subprocess,'run') as run, mock.patch('builtins.input') as prompt:
            self.assertEqual(self.run_flash('--dry-run'),0)
            run.assert_not_called();prompt.assert_not_called()

    def test_cancel_never_accesses_device(self):
        with mock.patch.object(first_flash.subprocess,'run') as run, mock.patch('builtins.input',return_value='NO'):
            self.assertEqual(self.run_flash(),1);run.assert_not_called()

    def test_wrong_flash_capacity_prevents_erase(self):
        with mock.patch.object(first_flash.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'Detected flash size: 8MB')) as run:
            with self.assertRaisesRegex(ValueError,'16MB'):self.run_flash('--confirm-erase')
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][-1],'flash_id')

    def test_detection_failure_prevents_erase(self):
        with mock.patch.object(first_flash.subprocess,'run',side_effect=subprocess.CalledProcessError(2,'probe')) as run:
            with self.assertRaises(subprocess.CalledProcessError):self.run_flash('--confirm-erase')
            self.assertEqual(run.call_count,1)

    def test_success_checks_capacity_then_writes_factory_at_zero(self):
        with mock.patch.object(first_flash.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'Detected flash size: 16MB')) as run:
            self.assertEqual(self.run_flash('--confirm-erase'),0)
            self.assertEqual(run.call_count,2)
            args=run.call_args_list[1].args[0]
            self.assertIn('--erase-all',args);self.assertNotIn('--force',args)
            self.assertEqual(args[-2:],['0x0',str(self.stage/'factory.bin')])

    def test_write_failure_does_not_report_success(self):
        responses=[subprocess.CompletedProcess([],0,'Detected flash size: 16MB'),subprocess.CalledProcessError(2,'write')]
        with mock.patch.object(first_flash.subprocess,'run',side_effect=responses):
            with self.assertRaises(subprocess.CalledProcessError):self.run_flash('--confirm-erase')


if __name__ == '__main__': unittest.main()
