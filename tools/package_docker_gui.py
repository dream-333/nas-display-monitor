#!/usr/bin/env python3
"""Export a prebuilt runtime as an offline, local-build Compose GUI project."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from release_config import ROOT, HOST_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['docker', 'podman'], default='docker')
    parser.add_argument('--image', default=f'nas-display:{HOST_VERSION}')
    parser.add_argument('--output', type=Path, default=ROOT / '.build/docker')
    args = parser.parse_args()
    def run(*cmd):
        return subprocess.check_output([args.engine, *cmd], text=True).strip()
    info = json.loads(run('image', 'inspect', args.image))[0]
    cfg = info['Config']
    assert info['Architecture'] == 'amd64' and info['Os'] == 'linux'
    assert cfg['Labels']['org.opencontainers.image.version'] == HOST_VERSION
    assert cfg['Labels']['org.nas-display.driver.kernel'] == '6.1.0-39-amd64'
    assert cfg['User'] == '10001:10001'
    assert cfg['Entrypoint'] == ['python', '/app/docker/entrypoint.py']
    assert not cfg.get('Cmd')
    name = f'NAS-Display-Compose-{HOST_VERSION}-amd64'
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-display-compose-') as temp:
        stage = Path(temp) / name
        stage.mkdir()
        container = 'nas-display-export-' + secrets.token_hex(6)
        try:
            # Never start this container: export a clean image, with no user data.
            run('create', '--name', container, '--network=none', args.image)
            raw = Path(temp) / 'runtime.tar'
            run('export', '-o', str(raw), container)
            with raw.open('rb') as source, gzip.open(stage/'runtime.tar.gz', 'wb', compresslevel=6) as dest:
                shutil.copyfileobj(source, dest)
        finally:
            subprocess.run([args.engine, 'rm', '-f', '-v', container], stdout=subprocess.DEVNULL, check=True)
        # Restore runtime metadata lost by export. No RUN, registry or compiler.
        lines = ['FROM scratch', 'ADD runtime.tar.gz /']
        for entry in cfg['Env']:
            key, value = entry.split('=', 1)
            lines.append('ENV ' + key + '=' + json.dumps(value))
        lines += ['WORKDIR ' + cfg['WorkingDir'], 'USER ' + cfg['User']]
        for key, value in cfg['Labels'].items():
            if key.startswith('org.'):
                lines.append('LABEL ' + key + '=' + json.dumps(value))
        lines += ['EXPOSE 8787', 'VOLUME ["/data"]',
                  'HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "/app/docker/healthcheck.py"]',
                  'ENTRYPOINT ' + json.dumps(cfg['Entrypoint'])]
        (stage/'Dockerfile').write_text('\n'.join(lines)+'\n')
        (stage/'.dockerignore').write_text('**\n!Dockerfile\n!runtime.tar.gz\n')
        compose = (ROOT/'compose.yaml').read_text().replace('    build: .\n', '')
        # Build-only services never try to pull an unpublished image. Four tags
        # may be created by Compose; the identical runtime layers are shared.
        compose = compose.replace(f'    image: nas-display:{HOST_VERSION}', '    build:\n      context: .\n      network: none')
        compose = compose.replace('      NAS_DISPLAY_PORT: ${NAS_DISPLAY_PORT:-8787}',
            '      # Replace the entire value below with your own quoted password before starting.\n'
            '      NAS_DISPLAY_ADMIN_PASSWORD: ${NAS_DISPLAY_ADMIN_PASSWORD:?请填写初始登录密码}\n'
            '      NAS_DISPLAY_PORT: 8787')
        (stage/'docker-compose.yml').write_text(compose)
        shutil.copyfile(ROOT/'docs/DOCKER-GUI.zh-CN.md', stage/'README.zh-CN.md')
        shutil.copyfile(ROOT/'THIRD-PARTY-NOTICES.md', stage/'THIRD-PARTY-NOTICES.md')
        (stage/'IMAGE.json').write_text(json.dumps({
            'version': HOST_VERSION, 'platform': 'linux/amd64', 'source_image_id': info['Id'],
            'driver_kernel': '6.1.0-39-amd64', 'target': 'Tieniu / Centerm Zero1 pro',
            'deployment': 'offline local Compose build; no registry, compiler or terminal on NAS',
            'target_hardware_verified': False, 'fnos_gui_verified': False,
        }, ensure_ascii=False, indent=2)+'\n')
        def digest(p):
            with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
        (stage/'SHA256SUMS.txt').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(stage.iterdir()) if p.is_file()))
        out = args.output/(name+'.zip')
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as z:
            for p in sorted(stage.iterdir()): z.write(p, name+'/'+p.name)
        with zipfile.ZipFile(out) as z: assert z.testzip() is None
        out.with_suffix('.zip.sha256').write_text(digest(out)+'  '+out.name+'\n')
        print(out)


if __name__ == '__main__': main()
