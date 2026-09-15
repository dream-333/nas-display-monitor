"""Load only the bundled, kernel-matched IT8613 module; never replace a driver."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import tempfile
import threading
import time

KERNEL = '6.1.0-39-amd64'
VERSION = 'nasdisplay-a904dd88'
BUNDLE = Path('/opt/it8613')
REPORT = Path('/run/nas-display-driver/status.json')


def read(path):
    try: return path.read_text().strip()
    except OSError: return ''


def insert_module(path):
    # x86_64 finit_module, flags=0: the kernel enforces vermagic, CRC and signing.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    with path.open('rb') as module:
        rc = libc.syscall(ctypes.c_long(313), ctypes.c_int(module.fileno()),
                          ctypes.c_char_p(b''), ctypes.c_int(0))
    if rc != 0:
        code = ctypes.get_errno()
        if code != errno.EEXIST: raise OSError(code, os.strerror(code))


def prepare(bundle=BUNDLE, sys=Path('/sys'), proc=Path('/proc'), kernel=None,
            arch=None, policy='auto', loader=insert_module, sleep=time.sleep):
    kernel = platform.release() if kernel is None else kernel
    arch = platform.machine() if arch is None else arch
    def report(state, message): return {'state': state, 'message': message, 'kernel': kernel}
    def found():
        return any(read(h / 'name').startswith('it8613') for h in (sys / 'class/hwmon').glob('hwmon*'))
    if policy not in ('auto', 'existing-only'):
        return report('needs_attention', '驱动策略无效，仅支持 auto 或 existing-only。')
    if found():
        return report('existing_only', '已发现 IT8613 接口，沿用现有驱动，未加载或替换模块。')
    if policy == 'existing-only':
        return report('existing_only', '仅使用现有驱动；未发现 IT8613 接口，其余监控继续运行。')
    if kernel != KERNEL or arch != 'x86_64':
        return report('unsupported', f'内置驱动只适配 {KERNEL} / x86_64；当前内核未加载此模块。')
    dmi = sys / 'class/dmi/id'
    if (read(dmi / 'board_vendor').casefold(), read(dmi / 'board_name').casefold()) != ('centerm', 'zero1 pro'):
        return report('unsupported', '未识别为已验证的 Centerm Zero1 pro 主板，未加载 IT8613 模块。')
    if (sys / 'module/it87').exists():
        return report('needs_attention', '已有 it87 模块但未发现 IT8613 接口，未卸载或替换现有驱动。')
    if read(proc / 'sys/kernel/modules_disabled') == '1':
        return report('needs_attention', '铁牛内核已禁用模块加载，容器无法启用 IT8613 驱动。')
    lockdown = read(sys / 'kernel/security/lockdown')
    if '[integrity]' in lockdown or '[confidentiality]' in lockdown:
        return report('needs_attention', '内核启用了 Lockdown；内置驱动未签名，需要系统信任的签名模块。')
    try:
        caps = next(line.split()[1] for line in read(proc / 'self/status').splitlines() if line.startswith('CapEff:'))
        if not int(caps, 16) & (1 << 16): raise ValueError('missing CAP_SYS_MODULE')
    except (StopIteration, ValueError, IndexError):
        return report('needs_attention', '驱动容器没有 SYS_MODULE 权限；请使用交付的 Compose 配置。')
    try:
        manifest = json.loads((bundle / 'MODULES.json').read_text())
        if manifest['kernel'] != KERNEL or manifest['driver_version'] != VERSION:
            raise ValueError('Wrong manifest')
        modules = manifest['modules']
        if not isinstance(modules, dict) or set(modules) != {'hwmon-vid.ko', 'it87.ko'}: raise ValueError('Wrong modules')
        for name, digest in modules.items():
            path = bundle / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Module checksum mismatch')
    except (OSError, ValueError, KeyError, TypeError):
        return report('needs_attention', '镜像中的驱动文件或校验信息不完整，未加载任何模块。')
    try:
        if not (sys / 'module/hwmon_vid').exists(): loader(bundle / 'hwmon-vid.ko')
        loader(bundle / 'it87.ko')
    except OSError as exc:
        if exc.errno in (errno.EPERM, errno.EACCES):
            message = '内核拒绝加载驱动，请检查模块权限、Secure Boot 和系统安全策略。'
        elif exc.errno in (getattr(errno, 'EKEYREJECTED', 129), getattr(errno, 'ENOKEY', 126)):
            message = '内核拒绝驱动签名，需要受信任的签名模块。'
        elif exc.errno in (errno.ENOEXEC, errno.EINVAL):
            message = '内核拒绝模块格式或版本，请确认铁牛使用原版 Debian 6.1.0-39-amd64 内核。'
        elif exc.errno in (errno.ENODEV, errno.EBUSY):
            message = '驱动未识别设备或硬件资源被占用；未使用强制识别或资源冲突绕过。'
        else:
            message = f'驱动加载失败（errno={exc.errno}），其余监控继续运行。'
        return report('needs_attention', message)
    for _ in range(10):
        if found() and read(sys / 'module/it87/version') == VERSION:
            return report('ready', 'IT8613 专用驱动已加载，已发现传感器接口；可在风扇页面查看通道。')
        sleep(.2)
    return report('needs_attention', '模块加载后未确认匹配的 IT8613 接口；未强制卸载，请检查主机内核日志。')


def publish(data, path=REPORT):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.status-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), 0o644)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main():
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    publish({'state': 'checking', 'message': '正在检查铁牛内核与 IT8613 驱动。', 'kernel': platform.release()})
    result = prepare(policy=os.environ.get('NAS_DISPLAY_DRIVER_POLICY', 'auto'))
    publish(result)
    print(result['message'], flush=True)
    # Stay alive so Docker restarts this check after a host reboot. Do not unload
    # modules on container exit: they belong to the shared host kernel.
    stopped.wait()


if __name__ == '__main__': main()
