"""Validate fixed ESP32-S3 images and compose a first-install image offline."""
import hashlib
import struct

FLASH_SIZE = 16 * 1024 * 1024
SEGMENTS = ((0x0, 'bootloader.bin'), (0x8000, 'partitions.bin'),
            (0xe000, 'boot_app0.bin'), (0x10000, 'firmware.bin'))


def validate_esp_image(raw):
    if len(raw) < 24 or raw[0] != 0xe9 or not 1 <= raw[1] <= 16:
        raise ValueError('Invalid ESP image header')
    if struct.unpack_from('<H', raw, 12)[0] != 9:
        raise ValueError('Image is not ESP32-S3')
    if raw[3] != 0x4f or raw[23] != 1:
        raise ValueError('Expected 16MB / 80MHz image with SHA256')
    pos = 24; checksum = 0xef
    for _ in range(raw[1]):
        if pos + 8 > len(raw): raise ValueError('Truncated segment header')
        length = struct.unpack_from('<I', raw, pos + 4)[0]; pos += 8
        if pos + length > len(raw): raise ValueError('Truncated segment')
        for b in raw[pos:pos+length]: checksum ^= b
        pos += length
    end = (pos // 16 + 1) * 16
    if end + 32 != len(raw) or raw[end-1] != checksum:
        raise ValueError('Invalid image size or checksum')
    if hashlib.sha256(raw[:end]).digest() != raw[end:]:
        raise ValueError('Invalid ESP image SHA256')


def validate_partitions(raw):
    expected = [('nvs', 1, 2, 0x9000, 0x5000), ('otadata', 1, 0, 0xe000, 0x2000),
                ('app0', 0, 0x10, 0x10000, 0x640000), ('app1', 0, 0x11, 0x650000, 0x640000),
                ('spiffs', 1, 0x82, 0xc90000, 0x360000), ('coredump', 1, 3, 0xff0000, 0x10000)]
    if len(raw) != 3072: raise ValueError('Unexpected partition table size')
    got = []
    for i in range(0, len(raw), 32):
        entry = raw[i:i+32]
        if entry[:2] == b'\xeb\xeb':
            if entry[16:] != hashlib.md5(raw[:i]).digest(): raise ValueError('Partition MD5 mismatch')
            if raw[i+32:] != b'\xff' * (len(raw)-i-32): raise ValueError('Unexpected partition suffix')
            break
        if entry[:2] != b'\xaa\x50': raise ValueError('Invalid partition entry')
        _, typ, sub, offset, size, label, flags = struct.unpack('<HBBII16sI', entry)
        got.append((label.rstrip(b'\x00').decode(), typ, sub, offset, size))
        if flags != 0: raise ValueError('Encrypted partition layout unsupported')
    else: raise ValueError('Missing partition MD5')
    if got != expected: raise ValueError('Partition layout differs from UI5.1')


def compose(assets):
    parts = {name: (assets/name).read_bytes() for _, name in SEGMENTS}
    validate_esp_image(parts['bootloader.bin']); validate_esp_image(parts['firmware.bin'])
    validate_partitions(parts['partitions.bin'])
    if len(parts['bootloader.bin']) > 0x8000 or len(parts['boot_app0.bin']) != 0x2000:
        raise ValueError('Invalid boot image bounds')
    if len(parts['firmware.bin']) > 0x640000: raise ValueError('Application exceeds app0')
    length = 0x10000 + len(parts['firmware.bin'])
    merged = bytearray(b'\xff' * length)
    end = 0
    for offset, name in SEGMENTS:
        if offset < end: raise ValueError('Overlapping images')
        end = offset + len(parts[name]); merged[offset:end] = parts[name]
    return bytes(merged), parts
