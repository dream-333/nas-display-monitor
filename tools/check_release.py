#!/usr/bin/env python3
"""Check the current source and rebuild delivery in a disposable directory."""
import argparse
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from release_config import ROOT, HOST_VERSION, TERMINAL_VERSION, LOGS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--export', type=Path, help='Copy verified output into a new directory')
    args = parser.parse_args()
    if args.export and args.export.exists():
        raise SystemExit('Export destination must not exist')
    LOGS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-release-check-') as temp:
        env = dict(os.environ, NAS_DISPLAY_OUTPUT_DIR=temp, NAS_DISPLAY_LOG_DIR=str(LOGS),
                   PYTHONDONTWRITEBYTECODE='1')
        jobs = [([sys.executable, str(ROOT/'tools/verify_layout.py')], 'layout-check.log'),
                ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], f'host-{HOST_VERSION}-tests.log')]
        jobs += [([sys.executable, '-m', 'unittest', 'discover', '-s', 'src/terminal/tests', '-v'], f'terminal-{TERMINAL_VERSION}-tests.log')]
        jobs += [([sys.executable, str(ROOT/'tools'/script)], log) for script, log in [
            ('build_host_deb.py', f'deb-{HOST_VERSION}-build.log'),
            ('build_host_fpk.py', f'fpk-{HOST_VERSION}-build.log'),
            ('build_terminal_fpk.py', f'terminal-{TERMINAL_VERSION}-build.log'),
            ('package_it87_driver.py', 'driver-package-check.log'),
            ('package_case.py', 'case-package-check.log'),
            ('package_firmware.py', 'first-flash-package-check.log'),
            ('verify_host_package.py', f'host-{HOST_VERSION}-package-check.log'),
            ('verify_host_fpk.py', f'fpk-{HOST_VERSION}-check.log'),
            ('package_release.py', f'release-{HOST_VERSION}-check.log'),
            ('package_source.py', f'source-{HOST_VERSION}-check.log'),
            ('manifest_dist.py', f'manifest-{HOST_VERSION}-check.log')]]
        index = next(i for i, (cmd, _) in enumerate(jobs) if cmd[-1].endswith('package_release.py'))
        jobs.insert(index, ([sys.executable, str(ROOT/'tools/verify_terminal_fpk.py'), '--no-screenshots'], f'terminal-{TERMINAL_VERSION}-check.log'))
        for command, name in jobs:
            path = LOGS/name
            with path.open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                raise SystemExit(f'FAILED: {name}; inspect {path}')
            print('PASS', name, flush=True)
        if args.export:
            shutil.copytree(temp, args.export)
            print('PASS exported verified delivery:', args.export, flush=True)
    print('PASS source/build/browser/packaging workflow; dist releases were not overwritten.')


if __name__ == '__main__': main()
