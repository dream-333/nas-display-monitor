#!/usr/bin/env python3
"""Extract FPK, exercise real UDS/SSH/PTY/browser against an isolated fake NAS.

No system services, real NAS accounts, router settings or root permissions used.
Test dependencies: requirements-dev.txt; never shipped in the FPK.
"""
import argparse
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
from release_config import COMPONENTS as DIST, TERMINAL_VERSION
import paramiko
from playwright.sync_api import sync_playwright, expect

PASSWORD = 'isolated-terminal-test-password'
SSH_PASSWORD = 'isolated-ssh-test-password'
PREFIX = '/app/dream-terminal'
PACKAGE = DIST / f'NAS-Terminal-fnOS-{TERMINAL_VERSION}-x86.fpk'


class FakeNAS(paramiko.ServerInterface):
    def __init__(self):
        self.shell = threading.Event()
        self.size = None
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if username == 'test-user' and password == SSH_PASSWORD else paramiko.AUTH_FAILED
    def get_allowed_auths(self, username): return 'password'
    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_pty_request(self, channel, term, width, height, *args):
        self.size = (width, height); return True
    def check_channel_window_change_request(self, channel, width, height, *args):
        self.size = (width, height); return True
    def check_channel_shell_request(self, channel): self.shell.set(); return True


def extract(archive, target):
    for member in archive.getmembers():
        assert not member.name.startswith('/') and '..' not in Path(member.name).parts
        assert member.isfile() or member.isdir()
    archive.extractall(target, filter='data')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-screenshots', action='store_true', help='Skip Chrome screenshot capture, keep all interaction checks')
    args = parser.parse_args()
    evidence = ROOT / '.build/terminal-1.0.2'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dream-terminal-verify-') as temp:
        temp = Path(temp)
        with tarfile.open(PACKAGE) as archive: extract(archive, temp / 'package')
        package = temp / 'package'
        manifest = dict(line.split('=', 1) for line in (package / 'manifest').read_text().splitlines() if '=' in line)
        manifest = {k.strip(): v.strip() for k, v in manifest.items()}
        assert manifest['maintainer'] == manifest['distributor'] == 'Dream'
        assert manifest['appname'] == 'dream-terminal'
        assert manifest['version'] == '1.0.2'
        assert 'service_port' not in manifest
        assert manifest['checksum'] == hashlib.md5((package / 'app.tgz').read_bytes()).hexdigest()
        with tarfile.open(package / 'app.tgz') as archive: extract(archive, temp / 'app')
        target = temp / 'app'
        entry = json.loads((target / 'ui/config').read_text())['.url']['dream-terminal.main']
        assert entry['gatewayPrefix'] == entry['url'] == PREFIX
        assert entry['gatewaySocket'] == 'app.sock' and entry['allUsers'] is False
        for name in ('server.py', 'terminal.py', 'pty_child.py', 'lifecycle.py'):
            assert (target / name).read_bytes() == (ROOT / 'src/terminal' / name).read_bytes()
        for path in (package / 'cmd').iterdir(): subprocess.run(['sh', '-n', str(path)], check=True)
        assert not list(target.rglob('auth.json')) and not list(target.rglob('known_hosts'))
        spec = importlib.util.spec_from_file_location('terminal_lifecycle', target / 'lifecycle.py')
        lifecycle = importlib.util.module_from_spec(spec); spec.loader.exec_module(lifecycle)
        lifecycle.PYTHON = '/usr/bin/python3.12'
        state = temp / 'state'; state.mkdir(mode=0o700)
        unit_paths = []
        for name, content in lifecycle.units(target, state).items():
            dest = temp / name; dest.write_text(content); unit_paths.append(str(dest))
        subprocess.run(['systemd-analyze', 'verify', *unit_paths], check=True)
        # Generate only disposable credentials, using the extracted runtime.
        subprocess.run(['/usr/bin/python3.12', '-I', '-c',
            'import sys,json,pathlib; sys.path.insert(0,sys.argv[1]); from server import password_record; '
            'pathlib.Path(sys.argv[2]).write_text(json.dumps(password_record(sys.argv[3])))',
            str(target), str(state / 'auth.json'), PASSWORD], check=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        uds = str(target / 'app.sock'); listener.bind(uds); listener.listen(16)
        ssh_listener = socket.socket(); ssh_listener.bind(('127.0.0.1', 0)); ssh_listener.listen(4); ssh_listener.settimeout(.2)
        (state / 'settings.json').write_text(json.dumps({'ssh_port': ssh_listener.getsockname()[1]}))
        ssh_key = paramiko.RSAKey.generate(2048)
        stop = threading.Event(); transports = []; servers = []; ssh_errors = []
        def ssh_client(conn):
            transport = paramiko.Transport(conn); transports.append(transport)
            try:
                transport.add_server_key(ssh_key); server = FakeNAS(); servers.append(server)
                transport.start_server(server=server); channel = transport.accept(15)
                if channel is None or not server.shell.wait(10): return
                channel.sendall(b'\x1b[38;5;85mDream test NAS\x1b[0m\r\n$ ')
                buffer = bytearray()
                while not stop.is_set():
                    data = channel.recv(4096)
                    if not data: break
                    for b in data:
                        if b == 3: buffer.clear(); channel.sendall(b'^C\r\n$ ')
                        elif b in (10, 13):
                            command = bytes(buffer).decode('utf-8', 'replace'); buffer.clear()
                            answer = 'test-user' if command == 'whoami' else 'fixture: ' + command
                            channel.sendall(('\r\n' + answer + '\r\n$ ').encode())
                        else: buffer.append(b); channel.sendall(bytes([b]))
            except (EOFError, OSError): pass
            except Exception as exc: ssh_errors.append(str(exc))
            finally: transport.close()
        def accept_ssh():
            while not stop.is_set():
                try: conn, _ = ssh_listener.accept()
                except socket.timeout: continue
                except OSError: break
                threading.Thread(target=ssh_client, args=(conn,), daemon=True).start()
        threading.Thread(target=accept_ssh, daemon=True).start()
        log = (evidence / 'server.log').open('w')
        # Simulate systemd fd 3 activation; this is the unmodified packaged main().
        shim = ('import os,sys; fd=int(sys.argv[1]); os.dup2(fd,3); os.set_inheritable(3,True); '
                'os.environ["LISTEN_FDS"]="1"; os.environ["LISTEN_PID"]=str(os.getpid()); '
                'os.execv(sys.executable,[sys.executable,"-I",sys.argv[2]])')
        server = subprocess.Popen(['/usr/bin/python3.12', '-I', '-c', shim, str(listener.fileno()), str(target / 'server.py')],
            pass_fds=(listener.fileno(),), env={**os.environ, 'DREAM_TERMINAL_STATE': str(state)}, stdout=log, stderr=log)
        def uds_request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('localhost', timeout=15)
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); conn.sock.settimeout(15); conn.sock.connect(uds)
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse(); result = (response.status, response.getheaders(), response.read()); conn.close(); return result
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.forward()
            def do_POST(self): self.forward()
            def forward(self):
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ('connection', 'host')}
                # Model a gateway that reserves Authorization for its own login.
                # Application bearer tokens must use X-Dream-Session instead.
                if self.headers.get('Authorization'):
                    raw = b'Unauthorized\n'
                    self.send_response(200); self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                    return
                headers.update({'Host': self.headers['Host'], 'X-Trim-Userid': '1000', 'X-Trim-Isadmin': 'true', 'X-Trim-Username': 'test-user'})
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                code, response_headers, raw = uds_request(self.command, self.path, body, headers)
                self.send_response(code)
                for key, value in response_headers:
                    # Regression: a proxy can return valid JSON with an
                    # incorrect media type. The 1.0.0 frontend lost the token
                    # and reported login expiry on a successful connect.
                    if key.lower() == 'content-type' and self.path.endswith('/api/connect') and code == 200:
                        value = 'text/html; charset=utf-8'
                    if key.lower() not in ('connection', 'transfer-encoding', 'server', 'date'): self.send_header(key, value)
                self.end_headers()
                try: self.wfile.write(raw)
                except BrokenPipeError: pass
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            for _ in range(50):
                if server.poll() is not None: raise RuntimeError('Packaged service exited: ' + (evidence / 'server.log').read_text())
                try:
                    if uds_request('GET', PREFIX)[0] == 403: break
                except OSError: pass
                time.sleep(.1)
            else: raise RuntimeError('No response from packaged Unix socket service')
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('NAS_DISPLAY_BROWSER') or shutil.which('google-chrome'), headless=True,
                                           args=['--disable-gpu', '--disable-dev-shm-usage'])
                for mobile in (False, True):
                    context = browser.new_context(viewport={'width': 390 if mobile else 1360, 'height': 844 if mobile else 920},
                                                  is_mobile=mobile, has_touch=mobile)
                    page = context.new_page(); errors = []; requests = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda r: requests.append(r.url))
                    url = f'http://127.0.0.1:{proxy.server_port}{PREFIX}'
                    page.goto(url)
                    # Regression: neither 200 HTML nor 502 errors are reported
                    # as an expired login. No secrets or raw HTML are rendered.
                    page.route('**/api/login', lambda route: route.fulfill(status=502, content_type='text/html', body='<h1>Bad Gateway</h1>'))
                    page.locator('#password').fill(PASSWORD); page.locator('#unlock-form button').click()
                    expect(page.locator('#error')).to_contain_text('HTTP 502')
                    expect(page.locator('#error')).not_to_contain_text('登录状态可能已过期')
                    page.unroute('**/api/login')
                    if not args.no_screenshots:
                        page.screenshot(path=str(evidence / ('mobile-login.png' if mobile else 'desktop-login.png')))
                    page.locator('#password').fill(PASSWORD); page.locator('#unlock-form button').click()
                    expect(page.locator('#connect-form')).to_be_visible()
                    page.route('**/api/connect', lambda route: route.fulfill(status=200, content_type='text/plain', body='Unauthorized\n'))
                    page.locator('#connect-form button[type=submit]').click()
                    expect(page.locator('#error')).to_contain_text('HTTP 200')
                    expect(page.locator('#connect-form')).to_be_visible()
                    page.unroute('**/api/connect')
                    page.locator('#connect-form button[type=submit]').click()
                    expect(page.locator('#workspace')).to_be_visible()
                    def screen():
                        return page.evaluate("Array.from({length:term.buffer.active.length},(_,i)=>term.buffer.active.getLine(i)?.translateToString(true)||'').join('\\n')")
                    def wait_text(text):
                        for _ in range(100):
                            if text in screen(): return
                            page.wait_for_timeout(100)
                        raise AssertionError('Missing terminal text: ' + text + '\n' + screen())
                    wait_text('password:')
                    page.locator('.xterm-helper-textarea').focus(); page.keyboard.insert_text(SSH_PASSWORD); page.keyboard.press('Enter')
                    wait_text('Dream test NAS')
                    assert SSH_PASSWORD not in screen(), 'SSH password must not echo'
                    page.keyboard.insert_text('whoami'); page.keyboard.press('Enter'); wait_text('test-user')
                    page.locator('#larger').click()
                    page.locator('#paste').click(); page.locator('#paste-text').fill('你好终端')
                    page.locator('#paste-send').click(); page.locator('[data-key=enter]').click(); wait_text('fixture: 你好终端')
                    page.locator('[data-key=interrupt]').click(); wait_text('^C')
                    page.locator('#diagnostics').click(); page.locator('[data-command=ipv6]').click()
                    wait_text('ip -6 addr show scope global')
                    assert 'fixture: ip -6' not in screen(), 'diagnostic must not auto-execute'
                    page.locator('[data-key=enter]').click(); wait_text('fixture: ip -6')
                    # Offline reads recover while the same SSH client remains alive.
                    old_generation = page.evaluate('generation')
                    context.set_offline(True); page.wait_for_timeout(600); context.set_offline(False)
                    for _ in range(60):
                        if page.locator('#state').inner_text() == '终端已打开': break
                        page.wait_for_timeout(100)
                    assert page.evaluate('generation') == old_generation
                    page.locator('#paste').click(); page.locator('#paste-text').fill('after-reconnect')
                    page.locator('#paste-send').click(); page.locator('[data-key=enter]').click(); wait_text('fixture: after-reconnect')
                    if not args.no_screenshots:
                        page.screenshot(path=str(evidence / ('mobile-terminal.png' if mobile else 'desktop-terminal.png')))
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'horizontal page overflow'
                    page.locator('#disconnect').click(); expect(page.locator('#connect-form')).to_be_visible()
                    page.locator('#connect-form button[type=submit]').click(); wait_text('password:')
                    page.locator('#lock').click(); expect(page.locator('#unlock-form')).to_be_visible()
                    # Simulate losing a page's token without its unload request.
                    # Re-unlock remains possible; recovery is explicit and scoped.
                    page.locator('#password').fill(PASSWORD); page.locator('#unlock-form button').click()
                    page.locator('#connect-form button[type=submit]').click(); wait_text('password:')
                    page.evaluate("token = ''; showWelcome()")
                    page.locator('#password').fill(PASSWORD); page.locator('#unlock-form button').click()
                    expect(page.locator('#old-sessions')).to_be_visible()
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#close-others').click()
                    expect(page.locator('#old-sessions')).to_be_hidden()
                    page.locator('#connect-form button[type=submit]').click(); wait_text('password:')
                    page.locator('#lock').click(); expect(page.locator('#unlock-form')).to_be_visible()
                    assert not errors, errors
                    assert all(u.startswith(f'http://127.0.0.1:{proxy.server_port}/') for u in requests), 'external runtime resource'
                    context.close()
                browser.close()
            assert (state / 'known_hosts').is_file()
            assert not ssh_errors, ssh_errors
        finally:
            proxy.shutdown(); proxy.server_close(); stop.set(); ssh_listener.close()
            for transport in transports: transport.close()
            server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
            listener.close(); log.close()
        contents = (evidence / 'server.log').read_text()
        assert PASSWORD not in contents and SSH_PASSWORD not in contents
    print('PASS: FPK metadata/checksum, shell hooks, unit syntax, bundled runtime over inherited Unix socket; real OpenSSH client to isolated password-auth SSH fixture; desktop/mobile browser, hidden SSH password, Unicode, Ctrl+C, resize, explicit diagnostic Enter, output reconnect, disconnect/lock, no external browser assets; 502 error classification, valid JSON with rewritten Content-Type, lost-token re-unlock and explicit old-connection recovery.')
    print('NOT TESTED: fnOS app-center lifecycle, real fnOS gateway/FN Connect, mobile app WebView, real NAS SSH account/sudo.')


if __name__ == '__main__': main()
