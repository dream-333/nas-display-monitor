#!/usr/bin/env python3
"""Build a native fnOS FPK with official fnpack, pinned dependencies and no user data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
from release_config import HOST_VERSION as VERSION, COMPONENTS as DIST, USER_DOCS, release_metadata
APP = 'nas-display-fnos'
DEPS = ROOT / 'third_party/tools/fnos'
OUT = DIST / f'NAS-Display-fnOS-{VERSION}-x86.fpk'


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    import cairosvg
    lock = json.loads((ROOT / 'src/host/fnos/vendor-lock.json').read_text())
    for name, digest in lock.items():
        assert hashlib.sha256((DEPS / name).read_bytes()).hexdigest() == digest, name
    with tempfile.TemporaryDirectory(prefix='nas-fpk-build-') as tmp:
        stage = Path(tmp) / APP
        def write(name, data, mode=0o644):
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(data)
            dest.chmod(mode)
        def put(source, name):
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, dest)
            dest.chmod(0o644)
        for name in ('app.py', 'cli.py', 'core.py', 'fan_curve.py', 'fan_hwmon.py', 'fan_client.py', 'hardware_status.py', 'usb_link.py', 'templates/index.html',
                     'static/app.js', 'static/fan-curves.js', 'static/app.css', 'static/icon.svg'):
            put('src/host/' + name, 'app/server/' + name)
        for name in ('collect.py', 'send.py', 'intel_pmu.py', 'storage.py', 'board.py', 'smart_cache.py'):
            put('src/collector/' + name, 'app/server/' + name)
        put('src/host/fnos/lifecycle.py', 'app/server/fpk_lifecycle.py')
        put('src/host/fnos/fpk_smart.py', 'app/server/fpk_smart.py')
        put('src/host/fnos/hardware.py', 'app/server/fpk_hardware.py')
        put('src/host/fnos/fan_control.py', 'app/server/fpk_fan.py')
        kit_files = ['INSTALL.sh', 'SOURCE.json'] + ['source/' + n for n in
                     ('it87.c', 'compat.h', 'Makefile', 'VERSION', 'dkms.conf', 'COPYING', 'README', 'SHA256SUMS')]
        for name in kit_files:
            put('hardware/drivers/it87/' + name, 'app/driver-kit/' + name)
        # Driver C code and DKMS version are exactly the kit validated on the NAS.
        subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=stage / 'app/driver-kit/source',
                       check=True, stdout=subprocess.DEVNULL)
        write('app/driver-kit/KIT-SHA256.json', json.dumps({n: hashlib.sha256((stage / 'app/driver-kit' / n).read_bytes()).hexdigest()
                                                         for n in kit_files}, indent=2) + '\n')
        put('docs/INSTALL.zh-CN.md', 'app/README.zh-CN.md')
        for name in USER_DOCS:
            put('docs/' + name, 'app/' + name)
        write('app/RELEASE.json', json.dumps(release_metadata(), indent=2) + '\n')
        for name in sorted(lock):
            if not name.endswith('.whl'):
                continue
            with zipfile.ZipFile(DEPS / name) as archive:
                assert not archive.testzip()
                assert all(not n.startswith('/') and '..' not in Path(n).parts for n in archive.namelist())
                archive.extractall(stage / 'app/vendor')
        # Bundle only smartctl and its license/source. Do not install smartd,
        # Debian maintainer scripts or smartd control code. The driver source
        # is a separate allowlisted kit, never a precompiled kernel module.
        smart = Path(tmp) / 'smartmontools'
        subprocess.run(['dpkg-deb', '-x', str(DEPS / 'smartmontools_7.3-1+b1_amd64.deb'), str(smart)], check=True)
        (stage / 'app/bin').mkdir(parents=True)
        shutil.copyfile(smart / 'usr/sbin/smartctl', stage / 'app/bin/smartctl')
        (stage / 'app/bin/smartctl').chmod(0o755)
        subprocess.run([str(stage / 'app/bin/smartctl'), '--version'], check=True, stdout=subprocess.DEVNULL)
        (stage / 'app/licenses/smartmontools').mkdir(parents=True)
        shutil.copyfile(smart / 'usr/share/doc/smartmontools/copyright', stage / 'app/licenses/smartmontools/copyright')
        for name in ('smartmontools_7.3.orig.tar.xz', 'smartmontools_7.3-1.debian.tar.xz', 'smartmontools_7.3-1.dsc'):
            shutil.copyfile(DEPS / name, stage / 'app/licenses/smartmontools' / name)
        put('src/host/fnos/vendor-lock.json', 'app/licenses/vendor-lock.json')
        write('app/licenses/SOURCES.txt', 'fnOS packaging specification: https://developer.fnnas.com/llms-full.txt\n'
              'fnpack 1.2.3: https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64 (build tool only)\n'
              'smartmontools 7.3-1+b1: https://deb.debian.org/debian/pool/main/s/smartmontools/\n'
              'Corresponding source, Debian patches and copyright are included here.\n'
              'Python dependencies: pinned PyPI wheels; licenses included in vendor/*.dist-info.\n')
        write('manifest', f'''appname={APP}
version={VERSION}
display_name=NAS Display 监控中心
desc=实时查看 CPU、GPU、SYS、全部硬盘温度、风扇及网络。无需连接屏幕，也可通过 USB 或 Wi-Fi 驱动 AMOLED 状态屏。
source=thirdparty
platform=x86
maintainer=Dream
distributor=Dream
install_dep_apps=python312
desktop_uidir=ui
desktop_applaunchname={APP}.main
service_port=8787
checkport=true
ctl_stop=true
disable_authorization_path=true
changelog=Dream 出品；移除 UDP 配对码和网页输入，兼容导入旧配置。Wi-Fi 屏幕须先升级免配对固件，再升级本应用。保留网页登录认证、USB 确认和 8787 端口。
''')
        write('config/privilege', json.dumps({'defaults': {'run-as': 'root'}, 'username': APP, 'groupname': APP, 'join-groups': ['dialout']}, indent=2) + '\n')
        write('config/resource', '{}\n')
        write('app/ui/config', json.dumps({'.url': {APP + '.main': {'title': 'NAS Display', 'icon': 'images/icon_{0}.png', 'type': 'url', 'protocol': 'http', 'port': '8787', 'url': '/', 'allUsers': False}}}, indent=2) + '\n')
        for size, name in ((64, 'ICON.PNG'), (256, 'ICON_256.PNG')):
            data = cairosvg.svg2png(url=str(ROOT / 'src/host/static/icon.svg'), output_width=size, output_height=size)
            (stage / name).write_bytes(data)
            dest = stage / f'app/ui/images/icon_{size}.png'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        def passwords(required=True):
            return [{'type': 'password', 'field': field, 'label': label, 'rules': [
                {'required': required, 'message': '请输入管理员密码'},
                {'min': 8, 'max': 128, 'message': '密码需要 8–128 个字符'}]}
                for field, label in (('wizard_password', '管理员密码'), ('wizard_confirm', '再次输入密码'))]
        driver_field = {'type': 'select', 'field': 'wizard_driver_policy', 'label': '风扇驱动准备', 'initValue': 'auto',
                        'options': [{'label': '自动准备已验证主板的驱动', 'value': 'auto'},
                                    {'label': '仅使用现有驱动，不安装', 'value': 'existing'}]}
        driver_tip = {'type': 'tips', 'helpText': '现有风扇接口可用时直接使用。Centerm Zero1 pro 缺少驱动时，会联网安装编译依赖及匹配当前内核的头文件，通过 DKMS 编译随包 IT8613 驱动。其他主板不自动加载未知驱动；不调整 PWM 或温控曲线。开发者 / 发布者：Dream。'}
        write('wizard/install', json.dumps([{'stepTitle': '开始监控', 'items': [
            {'type': 'tips', 'helpText': '网页使用 8787 端口，无需连接屏幕。首次安装默认不发送 USB 数据。请先卸载 Debian 版 nas-display-host，以释放相同的 8787 端口与 USB。Python 3.12 由应用中心安装。'},
            *passwords(), driver_field, driver_tip]}], ensure_ascii=False, indent=2))
        write('wizard/config', json.dumps([{'stepTitle': '密码与硬件准备', 'items': [
            {'type': 'tips', 'helpText': '填写两次新密码即可重置。留空保持当前密码，其他配置不变。'},
            *passwords(False), driver_field, driver_tip]}], ensure_ascii=False, indent=2))
        hook = '''#!/bin/sh
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
exec /var/apps/python312/target/bin/python3 -I "$TRIM_APPDEST/server/fpk_lifecycle.py" ACTION
'''
        for name, action in (('install_callback', 'install'), ('upgrade_callback', 'upgrade'),
                             ('uninstall_init', 'remove'), ('config_callback', 'configure')):
            write('cmd/' + name, hook.replace('ACTION', action), 0o755)
        write('cmd/main', hook.replace('ACTION', '"$1"'), 0o755)
        precheck = '''#!/bin/sh
set -eu
fail() { printf '%s\n' "$1" > "$TRIM_TEMP_LOGFILE"; exit 1; }
[ "$(uname -m)" = x86_64 ] || fail '此包适用于 x86-64 飞牛设备。'
[ -x /usr/bin/systemctl ] || fail '需要 systemd 服务管理器。'
if /usr/bin/systemctl is-active --quiet nas-display-host.service; then
  fail '请先执行 sudo apt remove nas-display-host 卸载旧 Debian 版，释放 8787 端口后重试。'
fi
'''
        # Runtime dependency is resolved by fnOS; check it after payload install.
        write('cmd/install_init', precheck, 0o755)
        write('cmd/upgrade_init', precheck + '''if [ "$(/usr/bin/systemctl show -p LoadState --value nas-display-fnos-fan.service)" = loaded ]; then
  /usr/bin/systemctl stop nas-display-fnos-fan.service
fi
if [ "$(/usr/bin/systemctl show -p LoadState --value nas-display-fnos-hardware.service)" = loaded ]; then
  /usr/bin/systemctl stop nas-display-fnos-hardware.service
fi
/usr/bin/systemctl stop nas-display-fnos-smart.timer nas-display-fnos-smart.service nas-display-fnos.service || true
''', 0o755)
        for name in ('uninstall_callback', 'config_init'):
            write('cmd/' + name, '#!/bin/sh\nexit 0\n', 0o755)
        for path in stage.rglob('*'):
            if path.is_dir(): path.chmod(0o755)
        # Official packer validates the native manifest/resource/wizard layout.
        subprocess.run([str(DEPS / 'fnpack'), 'build', '--directory', str(stage)], cwd=tmp, check=True)
        package = next(Path(tmp).rglob('*.fpk'))
        shutil.copyfile(package, OUT)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    Path(str(OUT) + '.sha256').write_text(digest + '  ' + OUT.name + '\n')
    cairosvg.svg2png(url=str(ROOT / 'src/host/static/icon.svg'), write_to=str(DIST / 'NAS-Display-icon.png'), output_width=512, output_height=512)
    print(OUT)


if __name__ == '__main__':
    main()
