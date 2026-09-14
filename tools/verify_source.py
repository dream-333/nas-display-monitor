"""Verify and rebuild from the exported ZIP, without the working tree's config."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile
from release_config import DIST, HOST_VERSION, LOGS, BUNDLE_NAME
from package_source import BLOCKED, PRIVATE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--export', type=Path, help='Export the successfully rebuilt delivery to a new directory')
    args = parser.parse_args()
    package = DIST / (BUNDLE_NAME + '-Source.zip')
    with tempfile.TemporaryDirectory(prefix='nas-source-handoff-') as tmp, zipfile.ZipFile(package) as z:
        assert z.testzip() is None
        prefix = BUNDLE_NAME + '-Source/'
        names = z.namelist()
        assert len(names) == len(set(names))
        for name in names:
            path = PurePosixPath(name)
            assert name.startswith(prefix) and not path.is_absolute() and '..' not in path.parts
            assert not BLOCKED.intersection(path.parts) and not PRIVATE.intersection(path.parts)
        manifest = json.loads(z.read(prefix+'SOURCE.json'))
        assert manifest['host'] == HOST_VERSION
        assert set(names) == {prefix+e['path'] for e in manifest['entries']} | {prefix+'SOURCE.json'}
        for e in manifest['entries']:
            raw = z.read(prefix+e['path'])
            assert len(raw) == e['bytes'] and hashlib.sha256(raw).hexdigest() == e['sha256']
        for info in z.infolist():
            path = Path(tmp)/info.filename
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(z.read(info))
            path.chmod(0o755 if info.external_attr >> 16 & 0o111 else 0o644)
        root = Path(tmp)/prefix
        assert not (root/'src/collector/config.json').exists()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', NAS_DISPLAY_LOG_DIR=str(LOGS/'source-rebuild'))
        env.pop('PYTHONPATH',None)
        command = [sys.executable, str(root/'tools/check_release.py')]
        if args.export: command += ['--export', str(args.export.resolve())]
        subprocess.run(command,cwd=root,env=env,check=True)
    print('PASS clean source extraction, all file hashes and complete rebuild without local config/cache/source paths')


if __name__ == '__main__': main()
