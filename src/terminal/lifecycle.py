#!/usr/bin/env python3
"""Root-only fnOS installation hooks; HTTP and SSH client run unprivileged."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

APP = 'dream-terminal'
PYTHON = '/var/apps/python312/target/bin/python3'
UNITS = (APP + '.socket', APP + '.service')


def quote(value):
    text = str(value)
    if any(c in text for c in '\r\n\x00'):
        raise ValueError('安装路径包含无效字符。')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def units(target, state):
    socket_path = str(Path(target) / 'app.sock')
    quote(socket_path)  # Reject control characters; ListenStream is not argv.
    socket = ('[Unit]\nDescription=NAS Terminal fnOS gateway socket\n'
              '[Socket]\nListenStream=' + socket_path.replace('%', '%%') + '\n'
              'SocketUser=root\nSocketGroup=root\nSocketMode=0666\nRemoveOnStop=true\n')
    # Socket activation keeps the entire executable payload root-owned. The
    # gateway's Unix user varies across fnOS versions; access to this local
    # socket alone NEVER substitutes for terminal-password and SSH auth.
    service = ('[Unit]\nDescription=NAS Terminal authenticated local SSH console\n'
               'Requires=dream-terminal.socket\nAfter=dream-terminal.socket network.target\n'
               '[Service]\nType=simple\nUser=dream-terminal\nGroup=dream-terminal\n'
               'ExecStart=' + quote(PYTHON) + ' -I ' + quote(Path(target) / 'server.py') + '\n'
               'Environment=' + quote('DREAM_TERMINAL_STATE=' + str(state)) + '\n'
               'Environment=PYTHONDONTWRITEBYTECODE=1\n'
               'Restart=on-failure\nRestartSec=3\nKillMode=control-group\nTimeoutStopSec=10\n'
               'UMask=0077\nNoNewPrivileges=true\nCapabilityBoundingSet=\n'
               'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\n'
               'ProtectKernelTunables=true\nProtectKernelModules=true\nProtectControlGroups=true\n'
               'RestrictSUIDSGID=true\nLockPersonality=true\n'
               'RestrictAddressFamilies=AF_UNIX AF_INET\n'
               'ReadWritePaths=' + quote(state) + '\nTasksMax=64\nMemoryMax=256M\n')
    return {UNITS[0]: socket, UNITS[1]: service}


def control(*args, check=True):
    return subprocess.run(['/usr/bin/systemctl', *args], check=check,
                          stdout=subprocess.DEVNULL).returncode


def atomic(path, value):
    fd, name = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main(action):
    if os.geteuid() != 0 or os.environ.get('TRIM_APPNAME') != APP:
        raise ValueError('请通过飞牛应用中心操作 NAS Terminal。')
    target = Path(os.environ['TRIM_APPDEST']).resolve()
    var = Path(os.environ['TRIM_PKGVAR']).resolve()
    if target == Path('/') or var == Path('/'):
        raise ValueError('安装路径无效。')
    state = var / 'state'
    if action == 'status':
        return 0 if control('is-active', '--quiet', UNITS[1], check=False) == 0 else 3
    if action in ('stop', 'remove'):
        control('stop', *UNITS, check=action == 'stop')
        if action == 'remove':
            for name in UNITS:
                (Path('/etc/systemd/system') / name).unlink(missing_ok=True)
            control('daemon-reload')
        return 0
    if action not in ('install', 'upgrade', 'configure', 'start'):
        raise ValueError('不支持的操作。')
    if not Path('/usr/bin/ssh').is_file():
        raise ValueError('系统缺少 /usr/bin/ssh 客户端。请先安装 openssh-client 后重试。')
    account = pwd.getpwnam(APP)
    if action in ('install', 'upgrade'):
        for path in [target, *target.rglob('*')]:
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('应用载荷包含不允许的文件类型。')
            os.chown(path, 0, 0)
            path.chmod(0o755 if path.is_dir() else 0o644)
    if state.is_symlink():
        raise ValueError('配置目录不能是符号链接。')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('auth.json', 'settings.json'):
        if (state / name).is_symlink():
            raise ValueError('配置文件不能是符号链接。')
    sys.path[:0] = [str(target), str(target / 'vendor')]
    from server import password_record
    if action in ('install', 'upgrade', 'configure'):
        password = os.environ.get('wizard_password', '')
        confirmation = os.environ.get('wizard_confirm', '')
        if not (state / 'auth.json').exists() or (action == 'configure' and (password or confirmation)):
            if password != confirmation:
                raise ValueError('两次终端密码不一致。')
            atomic(state / 'auth.json', password_record(password))
        current = json.loads((state / 'settings.json').read_text()) if (state / 'settings.json').exists() else {'ssh_port': 22}
        port = os.environ.get('wizard_ssh_port', '').strip() or str(current['ssh_port'])
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError('SSH 端口必须是 1–65535。')
        atomic(state / 'settings.json', {'ssh_port': int(port)})
    if not all((state / name).is_file() for name in ('auth.json', 'settings.json')):
        raise ValueError('尚未初始化，请重新完成安装向导。')
    os.chown(state, account.pw_uid, account.pw_gid)
    state.chmod(0o700)
    for name in ('auth.json', 'settings.json'):
        os.chown(state / name, account.pw_uid, account.pw_gid)
        (state / name).chmod(0o600)
    for name, content in units(target, state).items():
        dest = Path('/etc/systemd/system') / name
        dest.write_text(content)
        dest.chmod(0o644)
    control('daemon-reload')
    if action == 'configure':
        control('try-restart', UNITS[1])
    if action == 'start':
        control('start', *UNITS)
        import time
        time.sleep(1)
        if control('is-active', '--quiet', UNITS[1], check=False):
            raise ValueError('终端启动失败，请查看 dream-terminal.service 日志。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1]))
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else '终端操作失败，请检查应用日志和 Python 3.12 环境。'
        logfile = os.environ.get('TRIM_TEMP_LOGFILE')
        if logfile:
            Path(logfile).write_text(message + '\n')
        print(message, file=sys.stderr)
        raise SystemExit(1)
