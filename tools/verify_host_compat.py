"""Test Bookworm-era dependency APIs in a disposable venv, not a Debian OS test."""
from pathlib import Path
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='nas-host-bookworm-deps-') as directory:
    subprocess.run(['python3', '-m', 'venv', directory], check=True)
    python = str(Path(directory) / 'bin/python')
    subprocess.run([python, '-m', 'pip', 'install', 'Flask==2.2.2', 'Werkzeug==2.2.2',
                    'waitress==2.1.2', 'pyserial==3.5', 'Jinja2==3.1.2',
                    'itsdangerous==2.1.2', 'click==8.1.3'], check=True)
    subprocess.run([python, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT, check=True)
print('PASS Bookworm-era dependency versions on Ubuntu Python 3.12; not a Debian OS installation test')
