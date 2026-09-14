import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from terminal import Terminal, ssh_args, dimensions, MAX_OUTPUT
from server import create_app, password_record, PREFIX, IDLE, LIFETIME, UNCONNECTED_IDLE
from lifecycle import units

ADMIN = {'X-Trim-Userid': '1000', 'X-Trim-Isadmin': 'true', 'X-Trim-Username': 'test-user', 'X-Dream-Terminal': '1'}


class FakeTTY:
    def __init__(self, username, state, port, cols, rows):
        ssh_args(username, state, port)
        dimensions(cols, rows)
        self.last_input = time.monotonic()
        self.closed = False
        self.writes = []

    def close(self): self.closed = True
    def resize(self, cols, rows): dimensions(cols, rows)
    def write(self, data, seq): self.writes.append((data, seq))
    def read(self, offset): return {'data': base64.b64encode(b'hello\r\n').decode(), 'offset': 7, 'ended': False}


class APITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        (self.state / 'auth.json').write_text(json.dumps(password_record('test-only-password')))
        (self.state / 'settings.json').write_text('{"ssh_port":22}')
        self.app = create_app(self.state, FakeTTY)
        self.client = self.app.test_client()
        self.sessions = self.app.extensions['terminals']

    def tearDown(self):
        self.sessions.close()
        self.tmp.cleanup()

    def post(self, action, body=None, token='', headers=None):
        h = dict(ADMIN if headers is None else headers)
        if token: h['X-Dream-Session'] = token
        return self.client.post(PREFIX + '/api/' + action, json=body or {}, headers=h)

    def login(self):
        r = self.post('login', {'password': 'test-only-password'})
        self.assertEqual(r.status_code, 200)
        return r.json['token']

    def connect(self, token):
        r = self.post('connect', {'username': 'test-user', 'cols': 80, 'rows': 24}, token)
        self.assertEqual(r.status_code, 200)
        return self.sessions.items[token]['tty']

    def test_gateway_and_password_required(self):
        self.assertEqual(self.client.get(PREFIX).status_code, 403)
        for h in ({**ADMIN, 'X-Trim-Isadmin': 'false'}, {**ADMIN, 'X-Trim-Userid': ''}):
            self.assertEqual(self.post('login', {'password': 'test-only-password'}, headers=h).status_code, 403)
        self.assertEqual(self.post('read').status_code, 401)
        self.assertEqual(self.post('connect', {'username': 'test-user'}).status_code, 401)

    def test_csrf_and_no_cors(self):
        h = {**ADMIN, 'Sec-Fetch-Site': 'cross-site'}
        self.assertEqual(self.post('login', {'password': 'test-only-password'}, headers=h).status_code, 403)
        h = dict(ADMIN); del h['X-Dream-Terminal']
        self.assertEqual(self.post('login', {'password': 'test-only-password'}, headers=h).status_code, 403)
        r = self.client.post(PREFIX + '/api/login', data='password=test-only-password', headers=ADMIN)
        self.assertEqual(r.status_code, 403)
        r = self.client.options(PREFIX + '/api/login', headers={**ADMIN, 'Origin': 'https://attacker.invalid'})
        self.assertNotIn('Access-Control-Allow-Origin', r.headers)

    def test_cross_user_cannot_use_token(self):
        token = self.login(); self.connect(token)
        r = self.post('read', token=token, headers={**ADMIN, 'X-Trim-Userid': '1001'})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.post('read', token=token).status_code, 200)

    def test_login_throttle(self):
        for _ in range(5): self.assertEqual(self.post('login', {'password': 'wrong-password'}).status_code, 401)
        self.assertEqual(self.post('login', {'password': 'test-only-password'}).status_code, 429)

    def test_password_never_returned_or_cookie_stored(self):
        r = self.post('login', {'password': 'test-only-password'})
        self.assertNotIn('test-only-password', r.get_data(as_text=True))
        self.assertNotIn('Set-Cookie', r.headers)
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        self.assertIn("script-src 'self'", r.headers['Content-Security-Policy'])

    def test_bad_input_and_output_access(self):
        token = self.login()
        self.assertEqual(self.post('connect', {'username': '-oProxyCommand=evil'}, token).status_code, 400)
        self.assertEqual(self.post('connect', {'username': 'x', 'cols': 0}, token).status_code, 400)
        self.assertEqual(self.post('read', token=token).status_code, 400)
        self.connect(token)
        self.assertEqual(base64.b64decode(self.post('read', token=token).json['data']), b'hello\r\n')
        self.assertEqual(self.post('connect', {'username': 'x'}, token).status_code, 409)

    def test_logout_closes_session(self):
        token = self.login(); tty = self.connect(token)
        self.assertEqual(self.post('logout', token=token).status_code, 200)
        self.assertTrue(tty.closed)
        self.assertEqual(self.post('read', token=token).status_code, 401)

    def test_idle_poll_does_not_keep_shell_alive(self):
        token = self.login(); tty = self.connect(token)
        tty.last_input -= IDLE + 1
        self.assertEqual(self.post('read', token=token).status_code, 401)
        self.assertTrue(tty.closed)

    def test_absolute_expiration_and_capacity(self):
        token = self.login(); tty = self.connect(token)
        self.sessions.items[token]['born'] -= LIFETIME + 1
        self.sessions.sweep(); self.assertTrue(tty.closed)
        for _ in range(4): self.connect(self.login())
        fresh = self.login()
        self.assertEqual(self.post('connect', {'username': 'test-user'}, fresh).status_code, 409)
        self.assertEqual(self.post('sessions', token=fresh).json['own_connections'], 4)

    def test_repeated_unlocks_do_not_exhaust_terminal_slots(self):
        for _ in range(10): token = self.login()
        self.assertEqual(len(self.sessions.items), 1)
        self.assertEqual(self.post('sessions', token=token).json['active_connections'], 0)
        self.connect(token)

    def test_failed_password_does_not_evict_unused_unlock(self):
        token = self.login()
        self.assertEqual(self.post('login', {'password': 'wrong-password'}).status_code, 401)
        self.assertIn(token, self.sessions.items)

    def test_recovery_at_capacity_preserves_other_user(self):
        others = []
        for _ in range(3):
            token = self.login(); others.append(self.connect(token))
        foreign = self.login(); foreign_tty = self.connect(foreign)
        self.sessions.items[foreign]['uid'] = '1001'
        fresh = self.login()
        self.assertEqual(self.post('close_others', token='invalid').status_code, 401)
        self.assertEqual(self.post('close_others', token=fresh).status_code, 200)
        self.assertTrue(all(tty.closed for tty in others))
        self.assertFalse(foreign_tty.closed)
        self.assertIn(foreign, self.sessions.items)
        self.connect(fresh)

    def test_recovery_closes_own_lost_connect_response(self):
        token = self.login(); tty = self.connect(token)
        r = self.post('close_others', token=token)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(tty.closed)
        self.assertIsNone(self.sessions.items[token]['tty'])
        self.connect(token)

    def test_unused_unlock_expires_quickly(self):
        token = self.login()
        self.sessions.items[token]['idle'] -= UNCONNECTED_IDLE + 1
        self.sessions.sweep()
        self.assertNotIn(token, self.sessions.items)

    def test_gateway_authorization_header_does_not_replace_terminal_token(self):
        token = self.login()
        r = self.post('sessions', token=token, headers={**ADMIN, 'Authorization': 'Bearer gateway-token'})
        self.assertEqual(r.status_code, 200)

    def test_disconnect_and_reconnect(self):
        token = self.login(); tty = self.connect(token)
        born = self.sessions.items[token]['born']
        self.assertEqual(self.post('disconnect', token=token).status_code, 200)
        self.assertTrue(tty.closed)
        self.assertEqual(self.sessions.items[token]['born'], born)
        self.assertIsNot(self.connect(token), tty)

    def test_resource_bound_and_traversal(self):
        r = self.client.post(PREFIX + '/api/login', data='x' * 20000,
                             headers={**ADMIN, 'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.client.get(PREFIX + '/static/../server.py', headers=ADMIN).status_code, 404)
        self.assertEqual(self.client.get('/api/login', headers=ADMIN).status_code, 404)

    def test_missing_or_malformed_credentials_are_not_login_expiry(self):
        for record in ('invalid json', '{}', '{"salt":null,"digest":null}'):
            (self.state / 'auth.json').write_text(record)
            r = self.post('login', {'password': 'test-only-password'})
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json['code'], 'AUTH_CONFIG_UNAVAILABLE')
        (self.state / 'auth.json').unlink()
        self.assertEqual(self.post('login', {'password': 'test-only-password'}).status_code, 503)

    def test_unexpected_server_error_is_json_and_redacted(self):
        with patch('server.verify_password', side_effect=RuntimeError('sensitive data should not be logged')):
            with self.assertLogs(self.app.logger, level='ERROR') as captured:
                r = self.post('login', {'password': 'test-only-password'})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json['code'], 'INTERNAL_ERROR')
        self.assertNotIn('sensitive data', r.get_data(as_text=True))
        self.assertNotIn('sensitive data', ''.join(captured.output))
        self.assertIn(r.json['error_id'], ''.join(captured.output))

    def test_bad_json_has_json_error_response(self):
        r = self.client.post(PREFIX + '/api/login', data='{bad', headers={**ADMIN, 'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json['code'], 'HTTP_ERROR')


class PTYTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ttys = []

    def tearDown(self):
        for tty in self.ttys: tty.close()
        self.tmp.cleanup()

    def tty(self, code):
        obj = Terminal([sys.executable, '-u', '-c', code], self.tmp.name)
        self.ttys.append(obj)
        return obj

    def wait_for(self, tty, text):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            result = base64.b64decode(tty.read(0)['data'])
            if text in result: return result
            time.sleep(.04)
        self.fail('Timed out waiting for terminal output')

    def test_real_pty_unicode_input_and_duplicate_ack(self):
        tty = self.tty('import sys,tty; tty.setraw(0); print("READY",flush=True);\nwhile True:\n s=sys.stdin.readline(); print("RESULT:"+s,flush=True)')
        self.wait_for(tty, b'READY')
        tty.write('你好\n', 1)
        tty.write('你好\n', 1)
        data = self.wait_for(tty, 'RESULT:你好'.encode())
        time.sleep(.1)
        data = base64.b64decode(tty.read(0)['data'])
        self.assertEqual(data.count('RESULT:你好'.encode()), 1)
        with self.assertRaises(ValueError): tty.write('bad\n', 3)

    def test_pty_resize(self):
        tty = self.tty('import os,sys; print("READY",flush=True);\nfor line in sys.stdin: print("SIZE",os.get_terminal_size(0),flush=True)')
        self.wait_for(tty, b'READY'); tty.resize(65, 18); tty.write('\n', 1)
        self.wait_for(tty, b'columns=65, lines=18')

    def test_output_replay_bounds_and_utf8(self):
        tty = self.tty('import time; print("hello\\u4e16\\u754c",flush=True); time.sleep(60)')
        self.wait_for(tty, '世界'.encode())
        first = tty.read(0)
        self.assertEqual(first, tty.read(0))
        self.assertEqual(tty.read(first['offset'])['data'], '')
        with tty.lock:
            tty.output = bytearray(b'a' * MAX_OUTPUT); tty.total = MAX_OUTPUT + 500
        r = tty.read(0)
        self.assertTrue(r['truncated']); self.assertEqual(r['offset'], 500 + 32768)
        with self.assertRaises(ValueError): tty.read(-1)

    def test_close_reaps_client(self):
        tty = self.tty('import time; print("READY",flush=True); time.sleep(60)')
        self.wait_for(tty, b'READY'); tty.close()
        self.assertIsNotNone(tty.process.poll()); self.assertTrue(tty.closed)
        self.assertEqual(len(tty.output), 0)
        tty.close()

    def test_eof_and_bad_input(self):
        tty = self.tty('print("FINISHED",flush=True)')
        self.wait_for(tty, b'FINISHED')
        end = time.monotonic() + 3
        while not tty.eof and time.monotonic() < end: time.sleep(.04)
        self.assertTrue(tty.read(0)['ended'])
        with self.assertRaises(ValueError): tty.write('x' * 4097, 1)
        with self.assertRaises(ValueError): tty.resize(True, 20)

    def test_only_local_ssh_no_argument_injection(self):
        args = ssh_args('xfmeng2', self.tmp.name, 22)
        self.assertEqual(args[-1], '127.0.0.1')
        self.assertIn('StrictHostKeyChecking=accept-new', args)
        self.assertIn('PubkeyAuthentication=no', args)
        for name in ('-F/tmp/x', 'a b', 'a;whoami', 'x\ny', 'root@remote'):
            with self.assertRaises(ValueError): ssh_args(name, self.tmp.name, 22)
        for port in (0, 65536, True, '22'):
            with self.assertRaises(ValueError): ssh_args('x', self.tmp.name, port)

    def test_service_privilege_and_socket_only(self):
        out = units('/opt/dream-terminal', '/var/lib/dream-terminal')
        service = out['dream-terminal.service']
        self.assertIn('User=dream-terminal\n', service)
        self.assertIn('NoNewPrivileges=true', service)
        self.assertIn('KillMode=control-group', service)
        self.assertIn('ListenStream=/opt/dream-terminal/app.sock', out['dream-terminal.socket'])
        self.assertNotIn('8787', service)


if __name__ == '__main__': unittest.main()
