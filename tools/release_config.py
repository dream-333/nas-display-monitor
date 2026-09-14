"""Build-only release settings. Never imports or starts the host application."""
import ast
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
# Host version has one authoritative source; changing it changes artifact names.
TREE = ast.parse((ROOT / 'src/host/core.py').read_text())
HOST_VERSION = next(ast.literal_eval(n.value) for n in TREE.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))
DIST = Path(os.environ.get('NAS_DISPLAY_OUTPUT_DIR', ROOT / 'dist')).resolve()
LOGS = Path(os.environ.get('NAS_DISPLAY_LOG_DIR', ROOT / '.build/logs')).resolve()
FIRMWARE_VERSION = 'UI5.1'
ASSETS = ROOT / 'assets' / FIRMWARE_VERSION
FIRMWARE_ID = f'NAS-AMOLED-{FIRMWARE_VERSION}-USB'
RELEASE_NOTES = 'docs/FANS.zh-CN.md'

TERMINAL_TREE = ast.parse((ROOT / 'src/terminal/server.py').read_text())
TERMINAL_VERSION = next(ast.literal_eval(n.value) for n in TERMINAL_TREE.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))

# Packaging revision does not change runtime application versions.
DELIVERY_REVISION = 5
BUNDLE_NAME = f'NAS-Display-{HOST_VERSION}-r{DELIVERY_REVISION}'
COMPONENTS = DIST / 'components'
USER_DOCS = ('INSTALL.zh-CN.md', 'SENSORS.zh-CN.md', 'FANS.zh-CN.md',
             'DRIVERS.zh-CN.md', 'DISPLAY.zh-CN.md', 'FIRST-FLASH.zh-CN.md', 'TERMINAL.zh-CN.md', 'REMOTE-ACCESS.zh-CN.md',
             'CHANGELOG.zh-CN.md', 'VALIDATION.zh-CN.md')

def release_metadata():
    return {'host': HOST_VERSION, 'terminal': TERMINAL_VERSION,
            'firmware': FIRMWARE_VERSION, 'delivery_revision': DELIVERY_REVISION,
            'maintainer': 'Dream', 'distributor': 'Dream'}
