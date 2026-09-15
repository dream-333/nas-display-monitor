#!/usr/bin/env python3
"""Export an existing NAS Display image as a load-and-start Tieniu Debian NAS Docker bundle."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from release_config import ROOT, HOST_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=('docker', 'podman'), default='docker')
    parser.add_argument('--image', default=f'nas-display:{HOST_VERSION}')
    parser.add_argument('--output', type=Path, default=ROOT / '.build/docker')
    args = parser.parse_args()
    name = f'NAS-Display-Docker-{HOST_VERSION}-amd64'
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-docker-export-') as temp:
        stage = Path(temp) / name
        stage.mkdir()
        raw = Path(temp) / 'image.tar'
        command = [args.engine, 'save', '-o', str(raw)]
        if args.engine == 'podman': command += ['--format', 'docker-archive']
        subprocess.run(command + [args.image], check=True)
        with tarfile.open(raw) as archive:
            manifests = json.load(archive.extractfile('manifest.json'))
            assert len(manifests) == 1
            manifest = manifests[0]
            config = json.load(archive.extractfile(manifest['Config']))
            assert config['os'] == 'linux' and config['architecture'] == 'amd64'
            assert config['config']['Labels']['org.opencontainers.image.version'] == HOST_VERSION
            assert config['config']['User'] == '10001:10001'
            driver_kernel = config['config']['Labels'].get('org.nas-display.driver.kernel')
            assert driver_kernel == '6.1.0-39-amd64'
            tags = manifest['RepoTags']
            assert f'nas-display:{HOST_VERSION}' in tags or f'docker.io/library/nas-display:{HOST_VERSION}' in tags, tags
        with raw.open('rb') as source, gzip.open(stage / (name + '.tar.gz'), 'wb', compresslevel=6) as dest:
            shutil.copyfileobj(source, dest)
        # Offline install never rebuilds or needs the engineering checkout.
        compose = (ROOT / 'compose.yaml').read_text().replace('    build: .\n', '')
        (stage / 'compose.yaml').write_text(compose)
        shutil.copyfile(ROOT / 'src/host/docker/compose.usb.yaml', stage / 'compose.usb.yaml')
        shutil.copyfile(ROOT / 'docs/DOCKER.zh-CN.md', stage / 'README.zh-CN.md')
        shutil.copyfile(ROOT / 'THIRD-PARTY-NOTICES.md', stage / 'THIRD-PARTY-NOTICES.md')
        (stage / 'IMAGE.json').write_text(json.dumps({
            'host': HOST_VERSION, 'platform': 'linux/amd64', 'target': 'Tieniu NAS (Debian) / Centerm Zero1 pro / IT8613',
            'tags': tags, 'image_config': manifest['Config'], 'export_engine': args.engine,
            'target_hardware_verified': False, 'driver_kernel': driver_kernel,
            'driver_loading': 'guarded SYS_MODULE initialization; never replace or unload existing modules',
        }, ensure_ascii=False, indent=2) + '\n')
        def digest(path):
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest()
        (stage / 'SHA256SUMS.txt').write_text(''.join(digest(p) + '  ' + p.name + '\n'
                                                   for p in sorted(stage.iterdir()) if p.is_file()))
        out = args.output / (name + '.zip')
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as z:
            for p in sorted(stage.iterdir()): z.write(p, name + '/' + p.name)
        with zipfile.ZipFile(out) as z: assert z.testzip() is None
        out.with_suffix('.zip.sha256').write_text(digest(out) + '  ' + out.name + '\n')
        print(out)


if __name__ == '__main__': main()
