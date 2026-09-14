"""Read a bounded, sanitized hardware setup report; no privileged operations."""
import json
import os
from pathlib import Path
import time


def hardware_status():
    name = os.environ.get('NAS_DISPLAY_HARDWARE_STATUS')
    if not name: return None
    try:
        with Path(name).open() as f: raw = f.read(16385)
        if len(raw) > 16384: raise ValueError('oversize')
        data = json.loads(raw)
        if not isinstance(data, dict): raise ValueError('not a report')
        state = data.get('state')
        if state not in ('checking', 'ready', 'existing_only', 'unsupported', 'needs_attention',
                          'dependencies', 'preflight', 'building', 'verifying', 'cancelled'):
            raise ValueError('unknown state')
        return {'state': state, 'message': str(data.get('message', ''))[:500],
                'kernel': str(data.get('kernel', ''))[:128]}
    except (OSError, ValueError, TypeError):
        return {'state': 'waiting', 'message': '正在等待硬件检测。', 'kernel': ''}
