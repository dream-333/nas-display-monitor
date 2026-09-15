"""Isolated browser smoke test; never reads real config or opens a USB port."""
import argparse
import json
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/host'))
from core import atomic_json, default_config, password_record
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-dir', type=Path, default=ROOT / 'src/host')
    parser.add_argument('--server-python', default=sys.executable)
    parser.add_argument('--no-screenshots', action='store_true', help='Run functional checks without Chrome GPU capture')
    parser.add_argument('--curve-screenshots', action='store_true', help='Capture only the isolated curve fixture')
    args = parser.parse_args()
    previews = ROOT / '.build/previews'
    if not args.no_screenshots or args.curve_screenshots: previews.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nas-host-browser-') as tmp:
        state = Path(tmp)
        cfg = default_config()
        cfg['enabled'] = False  # Do not touch the user's display or running sender.
        atomic_json(state / 'config.json', cfg)
        password = 'isolated-browser-test-password'
        atomic_json(state / 'auth.json', password_record(password))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        with (state / 'server.log').open('w') as log:
            server = subprocess.Popen([args.server_python, '-s', str(args.app_dir / 'cli.py'), '--state-dir', str(state), 'serve', '--host', '127.0.0.1', '--port', str(port)], stdout=log, stderr=log)
            try:
                for _ in range(60):
                    try:
                        urllib.request.urlopen(url + '/api/session', timeout=.5).close();break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Isolated server did not start')
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=os.environ.get('NAS_DISPLAY_BROWSER') or shutil.which('google-chrome'), headless=True, args=['--disable-gpu','--disable-dev-shm-usage'])
                    page = browser.new_page(viewport={'width': 1440, 'height': 1100}, device_scale_factor=1)
                    errors = []
                    auth_transition = [False]
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and not (auth_transition[0] and '401' in msg.text) else None)
                    page.goto(url)
                    page.locator('#login-password').fill(password)
                    page.locator('#login-password').press('Enter')
                    page.locator('#app-view').wait_for(state='visible')
                    expect(page.locator('#cpu')).not_to_have_text('--', timeout=10000)
                    expect(page.locator('#send-state')).to_have_text('仅网页监控')
                    expect(page.locator('#poll-state')).to_have_text('实时采集中')
                    before = page.evaluate('currentStatus.uptime_seconds')
                    # wait_for_function internally uses eval, rejected by our CSP.
                    for _ in range(40):
                        if page.evaluate('currentStatus.uptime_seconds') > before + 1: break
                        page.wait_for_timeout(250)
                    else: raise AssertionError('Standalone web samples did not refresh')
                    assert page.evaluate('currentStatus.sample_age < 5 && currentStatus.sent === 0')
                    page.evaluate("updateStatus({...currentStatus, enabled:true, transport:'usb', display_error:'USB 未连接', error:'USB 未连接', sampling_error:null})")
                    expect(page.locator('#display-error')).to_contain_text('网页采集继续运行')
                    expect(page.locator('#connection-error')).to_be_hidden()
                    expect(page.locator('#cpu')).not_to_have_text('--')
                    page.evaluate('poll()')
                    page.evaluate("updateStatus({...currentStatus, hardware_setup:{state:'building',message:'正在编译驱动 <img src=x onerror=alert(1)>',kernel:'6.18-test'}})")
                    expect(page.locator('#hardware-setup')).to_be_visible()
                    expect(page.locator('#hardware-message')).to_contain_text('正在编译驱动')
                    assert page.locator('#hardware-setup img').count() == 0
                    expect(page.locator('#cpu')).not_to_have_text('--')
                    assert page.locator('#token').count() == 0
                    assert page.locator('#show-token').count() == 0
                    if not args.no_screenshots: page.screenshot(path=str(previews / 'host-web-overview.png'), full_page=True)
                    # Fixture rendering: never writes hardware or settings.
                    page.evaluate("""updateFans({fans:[
                        {driver:'nct6798',hwmon:'hwmon42',channel:'fan1',label:'CPU Fan',rpm:1380,status:'ok'},
                        {driver:'it87',hwmon:'hwmon8',channel:'fan2',label:'<img src=x onerror=alert(1)>',rpm:0,status:'ok',alarm:1},
                        {driver:'test',hwmon:'hwmon9',channel:'fan3',label:'Chassis',rpm:null,status:'unreadable'}],
                        fan_controls:[{driver:'nct6798',hwmon:'hwmon42',channel:'pwm1',duty_percent:50.2,enable:1}]},false)""")
                    expect(page.locator('#fan-list')).to_contain_text('1380 RPM')
                    expect(page.locator('#fan-list')).to_contain_text('0 RPM')
                    expect(page.locator('#fan-list')).to_contain_text('-- RPM')
                    assert page.locator('#fan-list img').count() == 0
                    assert page.locator('#fan-controls').count() == 0
                    page.evaluate("""updateThermals({disks:[{device:'sda',model:'<script>bad</script>',kind:'SATA',temperature_c:39,status:'ok',age_seconds:30},{device:'sdb',model:'Sleep disk',kind:'SATA',temperature_c:null,status:'sleeping',age_seconds:30}],board_temperatures:[{driver:'it8613',label:'temp2',temperature_c:48,status:'ok'}]},{id:'test',driver:'it8613',label:'temp2',temperature_c:48},false)""")
                    expect(page.locator('#sys-value')).to_have_text('48.0 °C')
                    expect(page.locator('#storage-count')).to_have_text('2 块硬盘')
                    expect(page.locator('#storage-list')).to_contain_text('休眠')
                    expect(page.locator('#storage-list')).to_contain_text('39.0 °C')
                    assert page.locator('#storage-list script').count() == 0
                    page.evaluate('updateThermals(currentStatus.sample || {},{},true)')
                    expect(page.locator('#sys-value')).to_have_text('--')
                    page.evaluate('updateFans(currentStatus.sample,true)')
                    expect(page.locator('#fan-count')).to_have_text('等待采样')
                    page.evaluate('updateFans({fans:[],fan_controls:[]},false)')
                    expect(page.locator('#fan-note')).to_contain_text('未暴露')
                    expect(page.locator('#fan-controls')).to_be_hidden()
                    page.locator('.nav[data-tab=settings]').click()
                    # Import an old config with an unusable pairing value: it is discarded.
                    legacy = dict(cfg, display_ip='127.0.0.1', token='ignored-legacy-code')
                    for key in ('transport', 'enabled', 'usb_port'):
                        legacy.pop(key, None)
                    page.locator('#import-config').set_input_files({
                        'name': 'legacy.json', 'mimeType': 'application/json',
                        'buffer': json.dumps(legacy).encode()})
                    expect(page.locator('#toast')).to_contain_text('旧配置已填入')
                    expect(page.locator('#transport')).to_have_value('udp')
                    expect(page.locator('#display-ip')).to_have_value('127.0.0.1')
                    expect(page.locator('#enabled')).not_to_be_checked()
                    assert page.locator('#token').count() == 0
                    page.locator('#transport').select_option('udp')
                    page.locator('#display-ip').fill('127.0.0.1')
                    page.locator('#interval').fill('1')
                    # Select a real read-only channel; verify the choice survives reload.
                    cpu_options = page.locator('#cpu-sensor option').evaluate_all('(items) => items.map(o => o.value).filter(v => v.startsWith("cpu-"))')
                    assert cpu_options, 'CPU channels were not exposed to the configuration form'
                    selected_cpu = cpu_options[-1]
                    page.locator('#cpu-sensor').select_option(selected_cpu)
                    page.locator('#config-form button[type=submit]').click()
                    expect(page.locator('#toast')).to_have_text('配置已保存')
                    assert 'token' not in json.loads((state / 'config.json').read_text())
                    assert json.loads((state / 'config.json').read_text())['interval'] == 1
                    assert json.loads((state / 'config.json').read_text())['cpu_sensor'] == selected_cpu
                    page.reload()
                    page.locator('#app-view').wait_for(state='visible')
                    page.locator('.nav[data-tab=settings]').click()
                    expect(page.locator('#interval')).to_have_value('1')
                    expect(page.locator('#cpu-sensor')).to_have_value(selected_cpu)
                    assert page.evaluate('currentStatus.cpu_temperature.id') == selected_cpu
                    assert page.evaluate('currentStatus.metrics.ct === currentStatus.cpu_temperature.value_c')
                    page.locator('#transport').select_option('usb')
                    if not args.no_screenshots: page.screenshot(path=str(previews / 'host-web-settings.png'), full_page=True)
                    page.locator('.nav[data-tab=overview]').click()
                    page.set_viewport_size({'width': 390, 'height': 844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    if not args.no_screenshots: page.screenshot(path=str(previews / 'host-web-mobile.png'), full_page=True)
                    page.set_viewport_size({'width': 1440, 'height': 1100})
                    # Intercept ONLY this isolated page's requests; no fan broker/hardware.
                    fan_fixture = {'available': True, 'error': None, 'channels': [
                        {'channel': 'pwm2', 'mode': 'auto', 'speed_percent': None, 'rpm': 1200,
                         'alarm': 0, 'temperature_c': 43, 'can_set': True, 'note': '自动起始值'},
                        {'channel': 'pwm3', 'mode': 'auto', 'speed_percent': None, 'rpm': 0,
                         'alarm': 1, 'temperature_c': 38, 'can_set': True, 'note': '请观察转速'}]}
                    generic = 'hwmon-0123456789abcdef-pwm1'
                    readonly = 'hwmon-fedcba9876543210-pwm1'
                    fan_fixture['channels'] += [
                        {'channel':generic,'label':'nct6798 / CPU Fan','mode':'auto','speed_percent':None,'rpm':1380,'temperature_c':None,'can_set':True,'can_auto':True,'note':'恢复模式 5'},
                        {'channel':readonly,'label':'<img src=x onerror=alert(1)>','mode':'unknown','speed_percent':None,'rpm':None,'temperature_c':None,'can_set':False,'can_auto':False,'note':'只读'}]
                    fan_posts = []
                    def fake_fans(route):
                        if route.request.method == 'POST':
                            body = route.request.post_data_json; fan_posts.append(body)
                            item = next(c for c in fan_fixture['channels'] if c['channel'] == body['channel'])
                            item.update(mode=body['mode'], speed_percent=body['speed_percent'])
                        route.fulfill(json=fan_fixture)
                    page.route('**/api/fans', fake_fans)
                    page.evaluate('pollFans()')
                    expect(page.locator('#fan-adjust-panel')).to_be_visible()
                    expect(page.locator('#pwm3-rpm')).to_have_text('0 RPM')
                    expect(page.locator('#pwm3-actual')).to_contain_text('风扇报警')
                    expect(page.locator('#pwm2-value')).to_be_disabled()
                    expect(page.locator('#pwm2-value')).to_have_value('')
                    assert 'PWM' not in page.locator('#fan-adjust-panel').inner_text()
                    page.locator('#pwm2-mode').select_option('manual')
                    page.locator('#pwm2-value').fill('58')
                    page.evaluate('pollFans()')
                    expect(page.locator('#pwm2-value')).to_have_value('58')
                    assert not fan_posts
                    page.locator('#pwm2-value').fill('101')
                    page.locator('.fan-adjust[data-channel=pwm2] button[type=submit]').click()
                    assert not fan_posts
                    page.locator('#pwm2-value').fill('58')
                    page.locator('.fan-adjust[data-channel=pwm2] button[type=submit]').click()
                    expect(page.locator('#pwm2-actual')).to_contain_text('58%')
                    assert fan_posts == [{'channel':'pwm2','mode':'manual','speed_percent':58}]
                    expect(page.locator('#pwm2-mode')).to_be_enabled()
                    page.locator('#pwm2-mode').select_option('auto')
                    page.locator('.fan-adjust[data-channel=pwm2] button[type=submit]').click()
                    expect(page.locator('#pwm2-actual')).to_contain_text('当前自动模式')
                    assert fan_posts[-1] == {'channel':'pwm2','mode':'auto','speed_percent':None}
                    expect(page.locator('#'+generic+'-rpm')).to_have_text('1380 RPM')
                    expect(page.locator('#'+readonly+'-rpm')).to_have_text('-- RPM')
                    expect(page.locator('#'+readonly+'-mode')).to_be_disabled()
                    assert page.locator('#fan-adjust-forms img').count() == 0
                    page.locator('#'+generic+'-mode').select_option('manual')
                    page.locator('#'+generic+'-value').fill('68')
                    page.locator('.fan-adjust[data-channel='+generic+'] button[type=submit]').click()
                    expect(page.locator('#'+generic+'-actual')).to_contain_text('68%')
                    assert fan_posts[-1] == {'channel':generic,'mode':'manual','speed_percent':68}
                    assert 'null' not in page.locator('#'+generic+'-actual').inner_text()
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.set_viewport_size({'width':1440,'height':1100})
                    # Mock only this isolated browser's curve API; no physical fan writes.
                    import fan_curve
                    fan_fixture['curve_presets'] = fan_curve.PRESETS
                    fan_fixture['curve_sources'] = [{'id':'cpu:auto','kind':'cpu','label':'CPU · 自动','temperature_c':50}]
                    fan_fixture['channels'][0].update(can_curve=True,can_auto=True,label='CPU 风扇',note='主板控制 · 可随时恢复自动模式')
                    curve_posts=[]
                    def fake_curve(route):
                        body=route.request.post_data_json;curve_posts.append(body)
                        c=fan_curve.validate(body['curve'])
                        fan_fixture['channels'][0].update(mode='curve',curve=c,curve_active=True,curve_temperature=50,
                            curve_target=fan_curve.target(c,50),speed_percent=fan_curve.target(c,50),curve_state='运行中',note='后台按温度曲线调节；关闭网页继续运行，主板自动可退出曲线。')
                        route.fulfill(json=fan_fixture)
                    page.route('**/api/fan-curve',fake_curve)
                    page.evaluate('pollFans()')
                    mode=page.locator('#pwm2-mode');expect(mode.locator('option[value=curve]')).to_have_count(1)
                    mode.select_option('curve')
                    editor=page.locator('.fan-adjust[data-channel=pwm2] .curve-editor')
                    expect(editor).to_be_visible()
                    editor.locator('.curve-preset').select_option('silent')
                    assert editor.locator('.curve-point').count()==4
                    editor.locator('summary').click()
                    editor.locator('[data-curve=interpolation]').select_option('step')
                    expect(editor.locator('[data-curve=min_percent]')).to_have_attribute('min','0')
                    editor.locator('[data-curve=min_percent]').fill('0')
                    expect(editor.locator('.curve-preset')).to_have_value('custom')
                    assert not curve_posts
                    page.evaluate('pollFans()')
                    expect(editor.locator('[data-curve=interpolation]')).to_have_value('step')
                    editor.locator('.curve-add').click()
                    assert editor.locator('.curve-point').count()==5
                    dot=editor.locator('.curve-point').nth(1).bounding_box()
                    page.mouse.move(dot['x']+dot['width']/2,dot['y']+dot['height']/2)
                    page.mouse.down();page.mouse.move(dot['x']+18,dot['y']-8,steps=3);page.mouse.up()
                    assert not curve_posts
                    page.locator('.fan-adjust[data-channel=pwm2] button[type=submit]').click()
                    expect(editor.locator('.curve-live-text')).to_contain_text('运行中')
                    assert len(curve_posts)==1 and curve_posts[0]['curve']['interpolation']=='step'
                    assert len(curve_posts[0]['curve']['points'])==5
                    assert curve_posts[0]['curve']['min_percent']==0
                    assert curve_posts[0]['curve']['points'][-1][1]==100
                    editor.locator('summary').click()
                    if not args.no_screenshots or args.curve_screenshots:
                        page.locator('#toast').wait_for(state='hidden',timeout=7000)
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    if not args.no_screenshots or args.curve_screenshots: page.locator('.fan-adjust[data-channel=pwm2]').screenshot(path=str(previews/'host-fan-curve-mobile.png'))
                    page.set_viewport_size({'width':1440,'height':1100})
                    if not args.no_screenshots or args.curve_screenshots: page.locator('.fan-adjust[data-channel=pwm2]').screenshot(path=str(previews/'host-fan-curve.png'))
                    mode.select_option('auto')
                    page.locator('.fan-adjust[data-channel=pwm2] button[type=submit]').click()
                    expect(editor).to_be_hidden()
                    auth_transition[0] = True  # In-flight polls can correctly return 401 after password rotation/logout.
                    page.locator('.nav[data-tab=account]').click()
                    page.locator('#current-password').fill(password)
                    page.locator('#new-password').fill('12345678')
                    page.locator('#confirm-password').fill('12345678')
                    expect(page.locator('#new-password')).to_have_attribute('minlength', '8')
                    page.locator('#password-form button[type=submit]').click()
                    page.locator('#login-view').wait_for(state='visible')
                    page.locator('#login-password').fill('12345678')
                    page.locator('#login-form button').click()
                    page.locator('#app-view').wait_for(state='visible')
                    page.locator('#logout').click()
                    page.locator('#login-view').wait_for(state='visible')
                    assert not errors, errors
                    browser.close()
                print('PASS browser login, live sampling, CPU source save/reload, mobile layout, password and logout; fan mode/percentage fixtures; curve preset, drag, point edits, explicit apply, live state, mobile layout and return to auto; no JS/CSP errors')
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    main()
