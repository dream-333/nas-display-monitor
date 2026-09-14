"""Package the verified V7 meshes and their exact supporting source files."""
import hashlib
import json
import zipfile
from release_config import ROOT, COMPONENTS as DIST, release_metadata


def main():
    base = ROOT / 'hardware/case'
    sums = base / 'full-case-v7-slide/SHA256SUMS.txt'
    names = []
    for line in sums.read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert not name.startswith('/') and '..' not in name.split('/')
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == digest, name
        names.append(name)
    names.append(str(sums.relative_to(base)))
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / 'AMOLED-Full-Case-V7-Slide-Lock.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('AMOLED-V7/RELEASE.json', json.dumps(release_metadata(), indent=2)+'\n')
        for name in sorted(names):
            z.write(base / name, 'AMOLED-V7/' + name)
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        for name in names: assert z.read('AMOLED-V7/' + name) == (base / name).read_bytes()
    out.with_name(out.name + '.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest() + '  ' + out.name + '\n')
    print('PASS unchanged V7 input hashes and ZIP entries:', out)


if __name__ == '__main__': main()
