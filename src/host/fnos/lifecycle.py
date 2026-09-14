#!/usr/bin/env python3
"""fnOS lifecycle only. Web runs as a package user; SMART has a separate unit."""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

# The hook invokes Python with -I; paths below are the root-owned package payload.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent / 'vendor')]

APP = 'nas-display-fnos'
WEB = APP + '.service'
SMART = APP + '-smart.service'
TIMER = APP + '-smart.timer'
HARDWARE = APP + '-hardware.service'
FAN = APP + '-fan.service'
POLICY = Path('/etc/nas-display-fnos-hardware.json')
PYTHON = '/var/apps/python312/target/bin/python3'
CACHE = '/run/nas-display-fnos-smart/disks.json'


def quote(value):
    # systemd specifiers and environment expansion are not shell escaping.
    value = str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('安装路径不能包含换行。')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def unit_files(target, state):
    server = Path(target) / 'server'
    vendor = Path(target) / 'vendor'
    env = ('Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\n'
           + 'Environment=' + quote('PYTHONPATH=' + str(vendor)) + '\n'
           + 'Environment=' + quote('NAS_DISPLAY_SMART_CACHE=' + CACHE) + '\n')
    hardening = ('NoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\n'
                 'PrivateTmp=true\nProtectKernelTunables=true\nProtectKernelModules=true\n'
                 'ProtectControlGroups=true\nRestrictSUIDSGID=true\nLockPersonality=true\n')
    web = ('[Unit]\nDescription=NAS Display fnOS web monitor\nAfter=network.target\n'
           '[Service]\nType=simple\nUser=nas-display-fnos\nGroup=nas-display-fnos\n'
           'SupplementaryGroups=dialout\nCapabilityBoundingSet=CAP_PERFMON\nAmbientCapabilities=CAP_PERFMON\n'
           + env + 'Environment=NAS_DISPLAY_HARDWARE_STATUS=/run/nas-display-fnos-hardware/status.json\n'
           + 'Environment=NAS_DISPLAY_FAN_SOCKET=/run/nas-display-fnos-fan/control.sock\n'
           + 'ExecStart=' + quote(PYTHON) + ' -s ' + quote(server / 'cli.py')
           + ' --state-dir ' + quote(state) + ' serve --port 8787\n'
           + 'Restart=on-failure\nRestartSec=5\nUMask=0077\nReadWritePaths=' + quote(state) + '\n'
           + hardening + 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n')
    smart = ('[Unit]\nDescription=NAS Display fnOS read-only SMART cache\n'
             '[Service]\nType=oneshot\nUser=root\n' + env
             + 'Environment=' + quote('NAS_DISPLAY_SMARTCTL=' + str(Path(target) / 'bin/smartctl')) + '\n'
             + 'ExecStart=' + quote(PYTHON) + ' -I ' + quote(server / 'fpk_smart.py') + '\n'
             + 'RuntimeDirectory=nas-display-fnos-smart\nRuntimeDirectoryMode=0755\nRuntimeDirectoryPreserve=yes\n'
             'TimeoutStartSec=300\nUMask=0022\nReadWritePaths=/run/nas-display-fnos-smart\n'
             + hardening + 'RestrictAddressFamilies=AF_UNIX\n')
    timer = ('[Unit]\nDescription=NAS Display fnOS disk temperature schedule\n'
             '[Timer]\nOnActiveSec=1s\nOnUnitInactiveSec=60s\nAccuracySec=5s\nUnit=' + SMART + '\n')
    hardware = ('[Unit]\nDescription=NAS Display hardware driver preparation\nAfter=network-online.target\nWants=network-online.target\n'
                '[Service]\nType=oneshot\nUser=root\n'
                'ExecStart=' + quote(PYTHON) + ' -I ' + quote(server / 'fpk_hardware.py') + '\n'
                'RuntimeDirectory=nas-display-fnos-hardware\nRuntimeDirectoryMode=0755\nRuntimeDirectoryPreserve=yes\n'
                'TimeoutStartSec=1800\nTimeoutStopSec=1800\nKillMode=mixed\nUMask=0022\n'
                'Environment=PYTHONDONTWRITEBYTECODE=1\nPrivateTmp=true\nProtectHome=true\n'
                'NoNewPrivileges=true\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n')
    fan = ('[Unit]\nDescription=Dream NAS Display local fan mode and PWM broker\n'
           'After=' + HARDWARE + '\n'
           '[Service]\nType=notify\nNotifyAccess=main\nWatchdogSec=30\nWatchdogSignal=SIGKILL\nTimeoutStartSec=300\nUser=root\nGroup=nas-display-fnos\n'
           'ExecStart=' + quote(PYTHON) + ' -I ' + quote(server / 'fpk_fan.py') + '\n'
           'RuntimeDirectory=nas-display-fnos-fan\nRuntimeDirectoryMode=0755\n'
           'StateDirectory=nas-display-fnos-fan\nStateDirectoryMode=0700\n'
           'Restart=on-failure\nRestartSec=2\nTimeoutStopSec=300\nUMask=0077\n'
           'NoNewPrivileges=true\nCapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_CHOWN\n'
           'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nProtectKernelModules=true\n'
           'ProtectControlGroups=true\nRestrictSUIDSGID=true\nRestrictAddressFamilies=AF_UNIX\n'
           'ReadWritePaths=/sys/devices\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n')
    return {WEB: web, SMART: smart, TIMER: timer, HARDWARE: hardware, FAN: fan}


def save_policy(value=None, path=POLICY):
    previous = json.loads(path.read_text()) if path.exists() else {'policy': 'auto'}
    policy = value if value is not None else previous.get('policy')
    if policy not in ('auto', 'existing'):
        raise ValueError('请选择自动准备已验证驱动或仅使用现有驱动。')
    if path.is_symlink(): raise ValueError('驱动策略文件路径异常。')
    from core import atomic_json
    atomic_json(path, {'policy': policy})


def initialize(state, password, confirmation):
    from core import atomic_json, default_config, password_record
    state = Path(state)
    present = [(state / name).is_file() for name in ('auth.json', 'config.json')]
    if all(present):
        return False  # Install retry / upgrade must preserve credentials and settings.
    if any(present):
        raise ValueError('已有不完整的配置，请先备份并检查应用数据目录。')
    if password != confirmation:
        raise ValueError('两次管理员密码不一致。')
    auth = password_record(password)
    cfg = default_config()
    cfg['enabled'] = False
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    atomic_json(state / 'config.json', cfg)
    atomic_json(state / 'auth.json', auth)
    return True


def reset_password(state, password, confirmation):
    from core import atomic_json, password_record
    if not password:
        return
    if password != confirmation:
        raise ValueError('两次管理员密码不一致。')
    auth = password_record(password)
    auth['secret'] = json.loads((state / 'auth.json').read_text())['secret']
    atomic_json(state / 'auth.json', auth)


def systemctl(*args, check=True):
    return subprocess.run(['/usr/bin/systemctl', *args], check=check,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def stop_hardware():
    result = subprocess.run(['/usr/bin/systemctl', 'show', '-p', 'LoadState', '--value', HARDWARE],
                            capture_output=True, text=True, check=False)
    if result.stdout.strip() == 'loaded':
        systemctl('stop', HARDWARE)  # Do not replace running privileged code on failure.


def stop_fan():
    result = subprocess.run(['/usr/bin/systemctl', 'show', '-p', 'LoadState', '--value', FAN],
                            capture_output=True, text=True, check=False)
    if result.stdout.strip() == 'loaded': systemctl('stop', FAN)


def main(action):
    if os.geteuid() != 0:
        raise ValueError('应用生命周期需要 root；网页服务使用独立账户运行。')
    if os.environ.get('TRIM_APPNAME') != APP:
        raise ValueError('应用名称不匹配。')
    target = Path(os.environ['TRIM_APPDEST']).resolve()
    var = Path(os.environ['TRIM_PKGVAR']).resolve()
    # Runtime paths are supplied by fnOS, never accepted from a web request.
    if not target.is_absolute() or not var.is_absolute() or target == Path('/') or var == Path('/'):
        raise ValueError('无效的应用路径。')
    state = var / 'state'
    if action == 'status':
        return 0 if systemctl('is-active', '--quiet', WEB, check=False) == 0 else 3
    if action == 'stop':
        # Older FPKs do not have this unit yet.
        stop_hardware()
        stop_fan()
        systemctl('stop', TIMER, SMART, WEB)
        return 0
    if action == 'remove':
        stop_hardware()
        stop_fan()
        systemctl('stop', TIMER, SMART, WEB, check=False)
        for name in (WEB, SMART, TIMER, HARDWARE, FAN):
            (Path('/etc/systemd/system') / name).unlink(missing_ok=True)
        systemctl('daemon-reload')
        return 0  # User state and the Debian installation are not removed.
    if action not in ('install', 'upgrade', 'start', 'configure'):
        raise ValueError('不支持的生命周期操作。')
    account = pwd.getpwnam(APP)
    if action in ('install', 'upgrade'):
        # The web account must never be able to edit the root SMART helper,
        # Python imports, executable or lifecycle code.
        for path in [target, *target.rglob('*')]:
            if path.is_symlink():
                raise ValueError('应用载荷包含未允许的符号链接。')
            os.chown(path, 0, 0)
            path.chmod(0o755 if path.is_dir() or path == target / 'bin/smartctl' else 0o644)
        initialize(state, os.environ.get('wizard_password', ''), os.environ.get('wizard_confirm', ''))
        save_policy(os.environ.get('wizard_driver_policy'))
    if action == 'configure':
        reset_password(state, os.environ.get('wizard_password', ''), os.environ.get('wizard_confirm', ''))
        save_policy(os.environ.get('wizard_driver_policy'))
    if not all((state / n).is_file() for n in ('config.json', 'auth.json')):
        raise ValueError('尚未初始化管理员密码。请重新安装并填写安装向导。')
    os.chown(state, account.pw_uid, account.pw_gid)
    state.chmod(0o700)
    for name in ('config.json', 'auth.json'):
        os.chown(state / name, account.pw_uid, account.pw_gid)
        (state / name).chmod(0o600)
    for name, content in unit_files(target, state).items():
        path = Path('/etc/systemd/system') / name
        path.write_text(content)
        path.chmod(0o644)
    systemctl('daemon-reload')
    if action == 'configure':
        running = systemctl('is-active', '--quiet', WEB, check=False) == 0
        systemctl('try-restart', WEB)
        if running: systemctl('start', '--no-block', HARDWARE)
    if action == 'start':
        systemctl('start', WEB, TIMER)
        # Type=simple start does not guarantee a listening socket. Verify service
        # startup so a missing runtime/library is visible in the app center.
        import time
        time.sleep(1)
        if systemctl('is-active', '--quiet', WEB, check=False):
            raise ValueError('网页服务启动失败，请查看 journalctl -u nas-display-fnos。')
        # Queue both in one transaction: fan resume waits for driver preparation,
        # while the web remains available even if DKMS compilation takes time.
        systemctl('start', '--no-block', HARDWARE, FAN)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1]))
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else '应用操作失败，请查看应用日志或重新检查 Python 3.12 运行环境。'
        if os.environ.get('TRIM_TEMP_LOGFILE'):
            Path(os.environ['TRIM_TEMP_LOGFILE']).write_text(message + '\n')
        print(message, file=sys.stderr)
        raise SystemExit(1)
