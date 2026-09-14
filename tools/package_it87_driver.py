"""Package pinned GPL driver sources, never a development-host .ko."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
from release_config import COMPONENTS as DIST, release_metadata
SOURCE = ROOT / 'hardware/drivers/it87'
NAME = 'NAS-IT8613-Driver-Kit'


def main():
    entries = ['INSTALL.sh', 'README.zh-CN.md', 'FAN-CHECK.py', 'SOURCE.json'] + [
        'source/' + name for name in ('it87.c', 'compat.h', 'Makefile', 'VERSION',
                                     'dkms.conf', 'COPYING', 'README', 'SHA256SUMS')]
    for line in (SOURCE / 'source/SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert 'source/' + name in entries
        assert hashlib.sha256((SOURCE / 'source' / name).read_bytes()).hexdigest() == digest
    assert json.loads((SOURCE / 'SOURCE.json').read_text())['commit'] == 'a904dd88b295a1bd4eeb47af523d8fad1e566a9f'
    manifest = [{'path': name, 'sha256': hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()} for name in entries]
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / (NAME + '.zip')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(NAME + '/RELEASE.json', json.dumps(release_metadata(), indent=2) + '\n')
        for name in entries:
            archive.write(SOURCE / name, NAME + '/' + name)
        archive.writestr(NAME + '/FILES.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for item in manifest:
            assert hashlib.sha256(archive.read(NAME + '/' + item['path'])).hexdigest() == item['sha256']
        assert not any(name.endswith(('.ko', '.o')) or '/.git/' in name for name in archive.namelist())
    Path(str(output) + '.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name + '\n')
    print('PASS pinned source hashes, GPL source/license, ZIP CRC and no precompiled module:', output)


if __name__ == '__main__':
    main()
