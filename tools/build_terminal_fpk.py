#!/usr/bin/env python3
"""Build the separate NAS Terminal FPK; never overwrite NAS Display releases."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/terminal'
APP = 'dream-terminal'
from release_config import TERMINAL_VERSION as VERSION, COMPONENTS as DIST, release_metadata
DEPS = ROOT / 'third_party/tools/fnos'
OUT = DIST / f'NAS-Terminal-fnOS-{VERSION}-x86.fpk'


def main():
    DIST.mkdir(parents=True, exist_ok=True)
    import cairosvg
    lock = json.loads((ROOT / 'src/host/fnos/vendor-lock.json').read_text())
    assert hashlib.sha256((DEPS / 'fnpack').read_bytes()).hexdigest() == lock['fnpack']
    for name, data in json.loads((SOURCE / 'vendor-lock.json').read_text()).items():
        assert hashlib.sha256((SOURCE / 'static/vendor' / name).read_bytes()).hexdigest() == data['sha256']
    with tempfile.TemporaryDirectory(prefix='dream-terminal-') as tmp:
        stage = Path(tmp) / APP
        def write(name, value, mode=0o644):
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(value)
            dest.chmod(mode)
        def put(source, name):
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            dest.chmod(0o644)
        for name in ('server.py', 'terminal.py', 'pty_child.py', 'lifecycle.py', 'vendor-lock.json'):
            put(SOURCE / name, 'app/' + name)
        put(ROOT / 'docs/TERMINAL.zh-CN.md', 'app/README.zh-CN.md')
        put(ROOT / 'docs/VALIDATION.zh-CN.md', 'app/VALIDATION.zh-CN.md')
        write('app/RELEASE.json', json.dumps(release_metadata(), indent=2) + '\n')
        for path in (SOURCE / 'static').rglob('*'):
            if path.is_file():
                put(path, 'app/' + str(path.relative_to(SOURCE)))
        for name, digest in lock.items():
            if not name.endswith('.whl') or 'pyserial' in name:
                continue
            raw = DEPS / name
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == digest
            with zipfile.ZipFile(raw) as archive:
                assert not archive.testzip()
                assert all(not n.startswith('/') and '..' not in Path(n).parts for n in archive.namelist())
                archive.extractall(stage / 'app/vendor')
        put(ROOT / 'src/host/fnos/vendor-lock.json', 'app/licenses/python-vendor-lock.json')
        write('app/licenses/SOURCES.txt', 'Frontend: @xterm/xterm 5.5.0 and @xterm/addon-fit 0.10.0; MIT licenses in static/vendor.\n'
              'Sources and npm integrity recorded in vendor-lock.json. Python wheel licenses included in vendor/*.dist-info.\n'
              'Uses fnOS system OpenSSH client, not bundled. Gateway spec: https://developer.fnnas.com/docs/core-concepts/gateway-registration/\n')
        write('manifest', f'''appname={APP}
version={VERSION}
display_name=NAS Terminal
desc=通过飞牛统一网关打开本机 SSH 终端。支持手机快捷键、粘贴和字体缩放，无需新增外网端口。需要系统支持统一网关并开启本机 SSH。
source=thirdparty
platform=x86
maintainer=Dream
distributor=Dream
install_dep_apps=python312
desktop_uidir=ui
desktop_applaunchname={APP}.main
ctl_stop=true
disable_authorization_path=true
changelog=应用名称统一为 NAS Terminal，同步应用入口和网页标题；保留 1.0.1 的认证与会话修复。升级保留密码和 SSH 端口。
''')
        write('config/privilege', json.dumps({'defaults': {'run-as': 'root'}, 'username': APP, 'groupname': APP}, indent=2))
        write('config/resource', '{}\n')
        write('app/ui/config', json.dumps({'.url': {APP + '.main': {
            'title': 'NAS Terminal', 'icon': 'images/icon_{0}.png', 'type': 'url', 'protocol': '',
            'gatewayPrefix': '/app/' + APP, 'gatewaySocket': 'app.sock', 'url': '/app/' + APP,
            'allUsers': False, 'control': {'accessPerm': 'readonly'}}}}, ensure_ascii=False, indent=2))
        for size, name in ((64, 'ICON.PNG'), (256, 'ICON_256.PNG')):
            raw = cairosvg.svg2png(url=str(SOURCE / 'static/icon.svg'), output_width=size, output_height=size)
            (stage / name).write_bytes(raw)
            dest = stage / f'app/ui/images/icon_{size}.png'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        def wizard(required):
            return [{'stepTitle': '终端登录设置', 'items': [
                {'type': 'tips', 'helpText': '仅飞牛管理员可用。设置独立终端密码，进入后仍需 NAS 的 SSH 账户密码。请在飞牛设置中开启 SSH，无需向外网开放 SSH 端口。需支持统一网关的 fnOS；手机 App 入口兼容性需实机验证。' + (' 留空密码保持不变。修改设置会关闭当前终端。' if not required else '')},
                *[{'type': 'password', 'field': field, 'label': label, 'rules': [
                    {'required': required, 'message': '请输入终端密码'}, {'min': 8, 'max': 128, 'message': '密码需要 8–128 个字符'}]}
                  for field, label in (('wizard_password', '终端密码'), ('wizard_confirm', '再次输入密码'))],
                {'type': 'text', 'field': 'wizard_ssh_port', 'label': 'NAS 本机 SSH 端口',
                 **({'initValue': '22'} if required else {}),
                 'rules': [{'required': required, 'message': '请输入 SSH 端口'}]}]}]
        write('wizard/install', json.dumps(wizard(True), ensure_ascii=False, indent=2))
        write('wizard/config', json.dumps(wizard(False), ensure_ascii=False, indent=2))
        hook = '#!/bin/sh\nset -eu\nexport PATH=/usr/sbin:/usr/bin:/sbin:/bin\nexec /var/apps/python312/target/bin/python3 -I "$TRIM_APPDEST/lifecycle.py" ACTION\n'
        for name, action in (('install_callback', 'install'), ('upgrade_callback', 'upgrade'),
                             ('uninstall_init', 'remove'), ('config_callback', 'configure'), ('main', '"$1"')):
            write('cmd/' + name, hook.replace('ACTION', action), 0o755)
        precheck = '#!/bin/sh\nset -eu\n[ -x /usr/bin/ssh ] || { printf "%s\\n" "系统缺少 OpenSSH 客户端 /usr/bin/ssh。" > "$TRIM_TEMP_LOGFILE"; exit 1; }\n'
        write('cmd/install_init', precheck, 0o755)
        write('cmd/upgrade_init', precheck + '/usr/bin/systemctl stop dream-terminal.socket dream-terminal.service\n', 0o755)
        for name in ('uninstall_callback', 'config_init'):
            write('cmd/' + name, '#!/bin/sh\nexit 0\n', 0o755)
        for path in stage.rglob('*'):
            if path.is_dir(): path.chmod(0o755)
        subprocess.run([str(DEPS / 'fnpack'), 'build', '--directory', str(stage)], cwd=tmp, check=True)
        package = next(Path(tmp).rglob('*.fpk'))
        OUT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package, OUT)
    Path(str(OUT) + '.sha256').write_text(hashlib.sha256(OUT.read_bytes()).hexdigest() + '  ' + OUT.name + '\n')
    print(OUT)


if __name__ == '__main__':
    main()
