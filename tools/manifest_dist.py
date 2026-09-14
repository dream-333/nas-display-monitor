"""Require exactly the current delivery files and generate their checksum index."""
import hashlib
import json
import shutil
from release_config import ROOT, DIST, HOST_VERSION, TERMINAL_VERSION, FIRMWARE_VERSION, BUNDLE_NAME, release_metadata


def main():
    roles = {
        BUNDLE_NAME+'-Delivery.zip': '安装、终端、屏幕升级和外壳完整交付',
        BUNDLE_NAME+'-Source.zip': '可继续开发的干净工程源码',
        f'NAS-Display-fnOS-{HOST_VERSION}-x86.fpk': 'fnOS NAS Display',
        f'nas-display-host_{HOST_VERSION}_all.deb': 'Debian NAS Display',
        f'NAS-Terminal-fnOS-{TERMINAL_VERSION}-x86.fpk': '独立 NAS Terminal',
        f'NAS-Display-AMOLED-{FIRMWARE_VERSION}-firmware.bin': 'AMOLED 应用镜像，仅 0x10000',
        'AMOLED-Full-Case-V7-Slide-Lock.zip': 'V7 外壳',
        'NAS-IT8613-Driver-Kit.zip': 'IT8613 固定驱动源码',
        'NAS-Display-icon.png': '应用图标',
    }
    roles = {(name if name.startswith(BUNDLE_NAME) else 'components/'+name): role for name, role in roles.items()}
    if DIST != ROOT/'dist': shutil.copyfile(ROOT/'dist/README.zh-CN.md', DIST/'README.zh-CN.md')
    allowed = set(roles) | {n+'.sha256' for n in roles} | {'README.zh-CN.md','CONTENTS.json','SHA256SUMS.txt'}
    actual = {p.relative_to(DIST).as_posix() for p in DIST.rglob('*') if p.is_file()}
    if any(p.is_symlink() for p in DIST.rglob('*')): raise ValueError('Symlinks are not allowed in delivery')
    if actual-allowed: raise ValueError('Non-delivery files in output: '+', '.join(sorted(actual-allowed)))
    entries = []
    for name, role in roles.items():
        p = DIST/name
        if not p.is_file() or p.is_symlink(): raise ValueError('Missing delivery: '+name)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        sidecar = DIST/(name+'.sha256')
        expected = digest+'  '+p.name+'\n'
        if sidecar.exists() and sidecar.read_text()!=expected: raise ValueError('Checksum mismatch: '+name)
        sidecar.write_text(expected)
        entries.append({'file':name,'role':role,'bytes':p.stat().st_size,'sha256':digest})
    manifest = dict(release_metadata(), artifacts=entries)
    (DIST/'CONTENTS.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (DIST/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(DIST).as_posix()+'\n'
        for p in sorted(DIST.rglob('*')) if p.is_file() and p.name!='SHA256SUMS.txt'))
    print('PASS current-only delivery manifest and SHA256:', len(entries), 'artifacts')


if __name__ == '__main__': main()
