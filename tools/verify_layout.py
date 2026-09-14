"""Reject retired/generated source exports and broken owned-document links."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from release_config import ROOT
from package_source import selected


def check_links(paths, boundary):
    for path in paths:
        source = re.sub(r'```.*?```', '', path.read_text(), flags=re.S)
        for target in re.findall(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', source):
            target = target.strip().split(' "', 1)[0].strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            assert resolved.is_relative_to(boundary.resolve()), (path, target, 'outside export')
            assert resolved.exists(), (path, target, 'missing local link')


def main():
    for old in ('host-app', 'nas-probe', 'terminal-app', 'firmware', 'mechanical',
                'drivers', 'upstream', 'licenses', 'release-assets', 'tools/vendor'):
        assert not (ROOT/old).exists(), ('obsolete root directory', old)
    paths = selected()
    retired = {'src/firmware/src/backlight_test.cpp', 'src/firmware/boards/lilygo-t-display-s3.json',
               'hardware/drivers/it87/FAN-PULSE.py', 'assets/UI5.1/preview.html',
               'assets/UI5.1/preview.svg'}
    for p in paths:
        rel = p.relative_to(ROOT).as_posix()
        assert rel not in retired, rel
        assert p.suffix not in {'.log', '.npz', '.pyc'}, rel
        if rel.startswith('docs/'):
            assert not re.search(r'-(?:1\.\d|2026)|/UI\d', p.name), rel
    owned = [p for p in paths if p.suffix == '.md'
             and p.relative_to(ROOT).parts[0] != 'third_party'
             and '/official/' not in p.as_posix() and '/source/' not in p.as_posix()]
    check_links(owned, ROOT)
    print('PASS source layout, generated/retired-file exclusions and owned Markdown links')


if __name__ == '__main__': main()
