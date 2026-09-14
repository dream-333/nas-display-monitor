#!/usr/bin/env python3
"""NAS Display Host setup and production web server."""
import argparse
import getpass
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys

from core import CONFIG_KEYS, VERSION, Monitor, Settings, atomic_json, default_config, password_record, validate_config

DEFAULT_STATE = Path('/var/lib/nas-display-host')


def write_private(directory, name, data):
    atomic_json(directory / name, data)
    if directory == DEFAULT_STATE and os.geteuid() == 0:
        account = pwd.getpwnam('nas-display-host')
        os.chown(directory / name, account.pw_uid, account.pw_gid)


def new_password():
    password = getpass.getpass('管理员密码（至少 8 个字符）: ')
    if password != getpass.getpass('再次输入密码: '):
        raise ValueError('两次密码不一致。')
    return password_record(password)


def restart_installed(directory):
    if directory == DEFAULT_STATE:
        subprocess.run(['systemctl', 'restart', 'nas-display-host.service'], check=True)
        print('服务已重启，浏览器访问 http://NAS的IP:8787 （地址与端口可在 /etc/default/nas-display-host 修改）。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('setup', help='Initialize password and settings; never overwrite existing settings')
    setup.add_argument('--import-config', type=Path, help='Import the existing src/collector/config.json')
    sub.add_parser('passwd', help='Reset administrator password without changing display settings')
    serve = sub.add_parser('serve', help='Start the production web server')
    serve.add_argument('--host', default=os.environ.get('NAS_DISPLAY_LISTEN', '0.0.0.0'))
    serve.add_argument('--port', type=int, default=int(os.environ.get('NAS_DISPLAY_PORT', '8787')))
    serve.add_argument('--secure-cookie', action='store_true', default=os.environ.get('NAS_DISPLAY_SECURE_COOKIE') == '1')
    args = parser.parse_args()
    directory = args.state_dir.expanduser().absolute()
    try:
        if args.command in ('setup', 'passwd'):
            if directory == DEFAULT_STATE and os.geteuid() != 0:
                raise ValueError('安装版的初始化或密码重置需要 sudo。')
            if args.command == 'setup':
                if any((directory / f).exists() for f in ('auth.json', 'config.json')):
                    raise ValueError('已有配置，未覆盖。请使用 passwd 重置管理员密码，或在网页中修改配置。')
                cfg = default_config()
                if args.import_config:
                    old = json.loads(args.import_config.read_text())
                    if not isinstance(old, dict) or set(old) - CONFIG_KEYS:
                        raise ValueError('旧配置包含不支持的字段。')
                    cfg.update(old)
                    cfg['transport'] = old.get('transport', 'udp')
                    if cfg['display_ip'] == 'CHANGE_ME':
                        cfg['display_ip'] = ''
                    cfg['enabled'] = False
                cfg = validate_config(cfg)
                auth = new_password()
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
                if directory == DEFAULT_STATE:
                    account = pwd.getpwnam('nas-display-host')
                    os.chown(directory, account.pw_uid, account.pw_gid)
                write_private(directory, 'config.json', cfg)
                write_private(directory, 'auth.json', auth)
                print('初始化完成。发送默认暂停，请登录网页选择 USB 或 Wi-Fi，确认网卡后启用。')
            else:
                old = json.loads((directory / 'auth.json').read_text())
                auth = new_password()
                auth['secret'] = old['secret']
                write_private(directory, 'auth.json', auth)
                print('管理员密码已重置，已有登录失效；屏幕配置保持不变。')
            restart_installed(directory)
        else:
            if not 1 <= args.port <= 65535:
                raise ValueError('网页端口必须是 1–65535。')
            from app import create_app
            from waitress import create_server
            settings = Settings(directory)
            monitor = Monitor(settings)
            app = create_app(directory, monitor, args.secure_cookie)
            server = create_server(app, host=args.host, port=args.port, threads=4,
                                   max_request_body_size=32768, max_request_header_size=8192,
                                   connection_limit=32, channel_timeout=30)
            def stop(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            monitor.start()
            print(f'NAS Display Host {VERSION} listening on {args.host}:{args.port}', flush=True)
            try:
                server.run()
            except KeyboardInterrupt:
                pass
            finally:
                server.close()
                monitor.stop()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        # Never echo malformed config values or imported JSON fragments (may contain secrets).
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        print('操作失败：' + message.rstrip('。') + '。详细步骤见 README。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
