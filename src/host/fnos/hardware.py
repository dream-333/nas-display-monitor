#!/usr/bin/env python3
"""Privileged one-shot hardware setup, invoked only by the fnOS lifecycle unit.

No HTTP endpoint, no fan/PWM writes, no sensors-detect/I2C sweep, no forced IDs.
Only the previously verified Zero1 pro gets the bundled it87 fallback. All other
machines use existing kernel interfaces until their hardware is verified.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
POLICY = Path('/etc/nas-display-fnos-hardware.json')
RUNTIME = Path('/run/nas-display-fnos-hardware')
DRIVER_VERSION = 'nasdisplay-a904dd88'


def read(path):
    try: return Path(path).read_text().strip()
    except OSError: return ''


def fans(hwmon=Path('/sys/class/hwmon')):
    result = []
    for h in sorted(hwmon.glob('hwmon*')):
        for p in sorted(h.glob('fan*_input')):
            value = read(p)
            if re.fullmatch(r'[0-9]+', value):
                result.append({'driver': read(h / 'name')[:32], 'channel': p.stem, 'rpm': int(value)})
    return result


def platform_supported(dmi=Path('/sys/class/dmi/id')):
    # A board allowlist is an installation gate, not a sensor naming rule.
    return (read(dmi / 'board_vendor').casefold(), read(dmi / 'board_name').casefold()) == ('centerm', 'zero1 pro')


def secure_boot(efi=Path('/sys/firmware/efi')):
    if not efi.exists(): return 'disabled'  # Legacy boot.
    for p in (efi / 'efivars').glob('SecureBoot-*'):
        try:
            data = p.read_bytes()
            if len(data) == 5 and data[4] in (0, 1): return 'enabled' if data[4] else 'disabled'
        except OSError: pass
    return 'unknown'


def headers_valid(headers, kernel):
    if not all((headers / f).is_file() for f in ('Makefile', 'Module.symvers', 'include/generated/utsrelease.h')):
        return False
    match = re.search(r'#define UTS_RELEASE "([^"]+)"', read(headers / 'include/generated/utsrelease.h'))
    return bool(match and match[1] == kernel)


def compiler_package(headers):
    content = read(headers / '.config') + '\n' + read(headers / 'include/generated/compile.h')
    match = re.search(r'(?:CONFIG_CC_VERSION_TEXT=|LINUX_COMPILER\s+)"([^" ]+)', content)
    if not match: return None
    compiler = match[1]
    match = re.fullmatch(r'(?:x86_64-linux-gnu-)?gcc-([0-9]{1,2})', compiler)
    if match: return 'gcc-' + match[1]
    match = re.fullmatch(r'clang-([0-9]{1,2})', compiler)
    if match: return 'clang-' + match[1]
    return None


def verify_kit(kit):
    manifest = json.loads((kit / 'KIT-SHA256.json').read_text())
    expected = {'INSTALL.sh', 'SOURCE.json'} | {'source/' + n for n in
               ('it87.c', 'compat.h', 'Makefile', 'VERSION', 'dkms.conf', 'COPYING', 'README', 'SHA256SUMS')}
    if set(manifest) != expected: raise ValueError('驱动包文件清单异常。')
    for name, digest in manifest.items():
        p = kit / name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('驱动源码校验失败，未安装。')


class Setup:
    def __init__(self, kernel=None, hwmon=Path('/sys/class/hwmon'), dmi=Path('/sys/class/dmi/id'),
                 efi=Path('/sys/firmware/efi'), modules=Path('/lib/modules'), sys_modules=Path('/sys/module'),
                 runtime=RUNTIME, kit=ROOT / 'driver-kit', dkms=Path('/var/lib/dkms/it87'),
                 cmdline=Path('/proc/cmdline')):
        self.kernel = kernel or os.uname().release
        self.hwmon, self.dmi, self.efi = hwmon, dmi, efi
        self.headers = modules / self.kernel / 'build'
        self.sys_modules, self.runtime, self.kit = sys_modules, runtime, kit
        self.dkms, self.cmdline = dkms, cmdline
        self.cancelled = False

    def publish(self, state, message):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o755)
        data = {'state': state, 'message': message, 'kernel': self.kernel,
                'updated_at': time.time(), 'fans': fans(self.hwmon)}
        tmp = self.runtime / '.status.json.tmp'
        # Root-owned RuntimeDirectory; web can read but cannot write here.
        with tmp.open('w') as f:
            os.fchmod(f.fileno(), 0o644)
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.runtime / 'status.json')
        print(message, flush=True)

    def command(self, args, timeout=600, capture=False):
        if self.cancelled: raise InterruptedError('Stopped between installation stages')
        env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
               'HOME': '/root', 'DEBIAN_FRONTEND': 'noninteractive'}
        # SIGTERM to the unit is deferred until this package-manager/build
        # operation completes, rather than interrupting dpkg between writes.
        process = subprocess.Popen(args, env=env, start_new_session=True,
                                   stdout=subprocess.PIPE if capture else None,
                                   stderr=subprocess.STDOUT if capture else None, text=True)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.communicate()
            raise
        if self.cancelled: raise InterruptedError('Stopped between installation stages')
        return process.returncode, output or ''

    def installed(self, package):
        code, text = self.command(['/usr/bin/dpkg-query', '-W', '-f=${db:Status-Status}', package], capture=True)
        return code == 0 and text.strip() == 'installed'

    def packages(self, names):
        if not names: return True
        # Only names derived from this fixed list, kernel release, or validated
        # numeric compiler version ever reach apt. No web-supplied arguments.
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', n) for n in names):
            raise ValueError('内核或依赖名称不受支持，未执行安装。')
        self.publish('dependencies', '正在准备编译依赖和当前内核头文件；网页监控继续运行。')
        apt = ['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=60', '-o', 'Acquire::Retries=1',
               '-o', 'Acquire::http::Timeout=20', '-o', 'Acquire::https::Timeout=20']
        code, _ = self.command(apt + ['update'], timeout=300)
        if code: return False
        code, _ = self.command(apt + ['-y', '--no-remove', '--no-install-recommends',
                                    '-o', 'Dpkg::Options::=--force-confdef',
                                    '-o', 'Dpkg::Options::=--force-confold', 'install', *names], timeout=900)
        return code == 0

    def run(self, policy):
        self.publish('checking', '正在检查风扇接口与驱动。')
        existing = fans(self.hwmon)
        if existing:
            self.publish('ready', '风扇转速接口已可用，沿用现有驱动，未重新安装。')
            return
        if policy == 'existing':
            self.publish('existing_only', '已选择仅使用现有驱动；当前没有可读风扇转速接口。')
            return
        if policy != 'auto': raise ValueError('未知的驱动策略。')
        if not platform_supported(self.dmi):
            self.publish('unsupported', '此主板尚未验证自动驱动安装；继续使用内核已有传感器。')
            return
        if self.sys_modules.joinpath('it87').exists():
            self.publish('needs_attention', '已有 it87 驱动正在运行但没有转速读数；保留现有驱动，请检查接线或驱动日志。')
            return
        if secure_boot(self.efi) != 'disabled':
            self.publish('needs_attention', 'Secure Boot 已开启或状态无法确认，需要可信签名；未自动安装或关闭安全启动。')
            return
        if self.dkms.is_dir() and any(p.is_dir() and not p.is_symlink() and p.name != DRIVER_VERSION for p in self.dkms.iterdir()):
            self.publish('needs_attention', '已注册其他版本的 it87 DKMS，保留现有驱动，未安装依赖或替换版本。')
            return
        if re.search(r'(?:^|\s)it87\.', read(self.cmdline)):
            self.publish('needs_attention', '启动参数中已有 it87 自定义选项，需要人工核对；未更改启动参数。')
            return
        modprobe = next((str(p) for p in (Path('/usr/sbin/modprobe'), Path('/sbin/modprobe')) if p.is_file()), None)
        if modprobe:
            code, config = self.command([modprobe, '--showconfig'], capture=True)
            if code or re.search(r'^(?:options|install)\s+it87(?:\s|$)', config, re.M):
                self.publish('needs_attention', '已有 it87 加载选项或无法检查配置；保留配置，未安装依赖或加载驱动。')
                return
        verify_kit(self.kit)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+._-]*', self.kernel):
            raise ValueError('内核版本格式不受支持。')
        deps = [name for name in ('build-essential', 'dkms', 'kmod', 'python3') if not self.installed(name)]
        if not headers_valid(self.headers, self.kernel): deps.append('linux-headers-' + self.kernel)
        cc = compiler_package(self.headers)
        if cc and not self.installed(cc): deps.append(cc)
        if not self.packages(deps):
            self.publish('needs_attention', '依赖安装失败：请检查软件源、网络和当前内核头文件是否可用。详见驱动准备日志。')
            return
        if not headers_valid(self.headers, self.kernel):
            self.publish('needs_attention', '头文件缺失或与运行内核不匹配：' + self.kernel + '。未编译或加载驱动。')
            return
        # Header package may only now reveal the kernel's compiler.
        cc = compiler_package(self.headers)
        if cc and not self.installed(cc) and not self.packages([cc]):
            self.publish('needs_attention', '当前内核所需编译器 ' + cc + ' 不可用；未加载驱动。')
            return
        self.publish('preflight', '已匹配内核，正在校验源码及现有驱动配置。')
        code, _ = self.command(['/bin/bash', str(self.kit / 'INSTALL.sh'), '--check'])
        if code:
            self.publish('needs_attention', '驱动预检查未通过；现有驱动/配置未覆盖。请查看驱动准备日志。')
            return
        self.publish('building', '正在为当前内核编译、安装并加载 IT8613 驱动，完成后自动验证转速。')
        code, _ = self.command(['/bin/bash', str(self.kit / 'INSTALL.sh')], timeout=900)
        if code:
            self.publish('needs_attention', '驱动安装或加载未完成；未使用强制 ID 或 ACPI 绕过。请查看驱动准备日志。')
            return
        self.publish('verifying', '驱动已加载，正在读取风扇转速。')
        time.sleep(2)
        if any(f['driver'].startswith('it8613') for f in fans(self.hwmon)):
            self.publish('ready', 'IT8613 风扇转速已读取，已配置开机加载与 DKMS；没有修改风扇模式或温控曲线。')
        else:
            self.publish('needs_attention', '驱动已加载，但没有可读 IT8613 风扇转速。不会把缺失接口当作 0 RPM。')


def main():
    if os.geteuid() != 0: raise SystemExit('Run only as the fnOS hardware setup service')
    setup = Setup()
    RUNTIME.mkdir(exist_ok=True, mode=0o755)
    with (RUNTIME / 'install.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        signal.signal(signal.SIGTERM, lambda *_: setattr(setup, 'cancelled', True))
        try:
            policy = json.loads(POLICY.read_text())['policy']
            setup.run(policy)
        except InterruptedError:
            setup.publish('cancelled', '驱动准备已在当前操作完成后停止；重新启动应用可再次检查。')
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            setup.publish('needs_attention', '驱动准备未完成。网页监控继续运行；请检查驱动准备日志后在飞牛应用设置中重试。')


if __name__ == '__main__': main()
