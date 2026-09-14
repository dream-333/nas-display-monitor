"""Export a clean, checksummed engineering tree using explicit source roots.

No runtime config, historical archive, local environment or release binaries.
Bundled build tools and immutable UI5.1 inputs are intentional dependencies.
"""
import hashlib
import json
from pathlib import Path
import stat
import zipfile
from release_config import ROOT, DIST, HOST_VERSION, TERMINAL_VERSION, FIRMWARE_VERSION, BUNDLE_NAME, release_metadata

FILES = ('README.md', '.gitignore', 'requirements-dev.txt', 'requirements-design.txt',
         'THIRD-PARTY-NOTICES.md', 'assets/README.zh-CN.md', 'dist/README.zh-CN.md')
TREES = ('src', 'tools', 'tests', 'docs', 'hardware', 'third_party', 'assets/UI5.1')
BLOCKED = {'.git', '.venv', '.platformio', '.pio', '.build', '__pycache__',
           'diagnostics', '.host-state', 'node_modules'}
PRIVATE = {'config.json', 'auth.json', 'active.json', 'curves.json', 'known_hosts',
           '.env', 'id_rsa', 'id_ed25519'}


def selected(root=ROOT):
    paths = {root / name for name in FILES}
    for tree in TREES:
        for p in (root / tree).rglob('*'):
            rel = p.relative_to(root)
            if BLOCKED.intersection(rel.parts) or p.name in PRIVATE: continue
            if p.suffix in ('.pyc', '.pyo', '.ko', '.o', '.npz'): continue
            if p.suffix == '.log': continue
            if p.is_symlink(): raise ValueError('Unexpected source symlink: ' + str(rel))
            if p.is_file(): paths.add(p)
    for p in paths:
        if not p.is_file() or p.is_symlink(): raise ValueError('Missing source input: ' + str(p))
    return sorted(paths)


def main():
    paths = selected()
    name = BUNDLE_NAME + '-Source'
    entries = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    manifest = dict(release_metadata(), entries=entries)
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / (name + '.zip')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for p, e in zip(paths, entries):
            info = zipfile.ZipInfo(name + '/' + e['path'], (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if p.stat().st_mode & 0o111 else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
        z.writestr(name + '/SOURCE.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        for e in entries: assert hashlib.sha256(z.read(name + '/' + e['path'])).hexdigest() == e['sha256']
        assert not any(PRIVATE.intersection(Path(n).parts) or BLOCKED.intersection(Path(n).parts) for n in z.namelist())
    out.with_name(out.name + '.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest() + '  ' + out.name + '\n')
    print(f'PASS source allowlist, private-file exclusions, {len(entries)} hashes and ZIP CRC: {out}')


if __name__ == '__main__': main()
