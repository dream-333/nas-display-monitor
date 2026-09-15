#!/usr/bin/env python3
"""Initialize a persistent container instance, then exec the production server."""
import fcntl
import json
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import atomic_json, default_config, password_record, validate_config


def initialize(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / '.initialize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        auth_path, config_path = state / 'auth.json', state / 'config.json'
        # Credentials are the initialization marker; never replace existing ones.
        if auth_path.exists():
            if not config_path.exists():
                raise ValueError('已有登录信息但配置缺失，请恢复数据卷中的 config.json。')
            validate_config(json.loads(config_path.read_text()))
            return False
        if config_path.exists():
            cfg = validate_config(json.loads(config_path.read_text()))
        else:
            cfg = default_config()
        direct = os.environ.get('NAS_DISPLAY_ADMIN_PASSWORD', '')
        source = os.environ.get('NAS_DISPLAY_ADMIN_PASSWORD_FILE', '')
        if direct and source:
            raise ValueError('初始密码和密码文件只能选择一种。')
        password = Path(source).read_text().rstrip('\r\n') if source else direct
        generated = state / 'initial-password.txt'
        if not password:
            # Reuse the secret after interrupted initialization.
            if generated.exists():
                password = generated.read_text().strip()
            else:
                password = secrets.token_urlsafe(18)
                fd = os.open(generated, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as out:
                    out.write(password + '\n'); out.flush(); os.fsync(out.fileno())
        auth = password_record(password)
        atomic_json(config_path, cfg)
        atomic_json(auth_path, auth)
        return True


def main():
    os.umask(0o077)
    state = Path(os.environ.get('NAS_DISPLAY_STATE_DIR', '/data'))
    try:
        created = initialize(state)
    except (OSError, ValueError, KeyError) as exc:
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print('容器初始化失败：' + message, file=sys.stderr, flush=True)
        return 1
    # Password environment variables are initialization-only, not inherited by web workers.
    os.environ.pop('NAS_DISPLAY_ADMIN_PASSWORD', None)
    os.environ.pop('NAS_DISPLAY_ADMIN_PASSWORD_FILE', None)
    if created:
        print('初始化完成。访问 http://主机IP:' + os.environ.get('NAS_DISPLAY_PORT', '8787'), flush=True)
        if (state / 'initial-password.txt').exists():
            print('初始密码已保存：docker compose exec nas-display cat /data/initial-password.txt', flush=True)
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parents[1] / 'cli.py'),
                            '--state-dir', str(state), 'serve'])


if __name__ == '__main__':
    raise SystemExit(main())
