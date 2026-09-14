#!/usr/bin/env python3
"""NAS Terminal: fnOS gateway + separate password + local SSH identity."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent / 'vendor')]
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from terminal import Terminal

PREFIX = '/app/dream-terminal'
VERSION = '1.0.2'
IDLE = 15 * 60
UNCONNECTED_IDLE = 2 * 60
LIFETIME = 8 * 60 * 60
MAX_TERMINALS = 4
MAX_AUTH_SESSIONS = 16


def password_record(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise ValueError('终端密码需要 8–128 个字符。')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return {'salt': salt.hex(), 'digest': digest.hex()}


def verify_password(password, record):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']), n=16384, r=8, p=1)
    return hmac.compare_digest(digest.hex(), record['digest'])


class Sessions:
    def __init__(self, state, factory=Terminal.ssh):
        self.state = Path(state)
        self.factory = factory
        self.items = {}
        self.failures = []
        self.lock = threading.RLock()

    def sweep(self):
        now = time.monotonic()
        with self.lock:
            for token, item in list(self.items.items()):
                last = item['tty'].last_input if item['tty'] else item['idle']
                idle_limit = IDLE if item['tty'] else UNCONNECTED_IDLE
                if now - last > idle_limit or now - item['born'] > LIFETIME:
                    self.drop(token)

    def counts(self, uid):
        active = [item for item in self.items.values() if item['tty'] and not getattr(item['tty'], 'eof', False)]
        return {'own_connections': sum(item['uid'] == uid for item in active),
                'active_connections': len(active), 'max_connections': MAX_TERMINALS}

    def drop(self, token):
        item = self.items.pop(token, None)
        if item and item['tty']:
            item['tty'].close()

    def close(self):
        with self.lock:
            for token in list(self.items):
                self.drop(token)


def create_app(state, factory=Terminal.ssh):
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 16384
    sessions = Sessions(state, factory)
    app.extensions['terminals'] = sessions
    config = json.loads((Path(state) / 'settings.json').read_text())
    assets = Path(__file__).parent / 'static'

    @app.before_request
    def gateway():
        # Never offer a TCP listener. Additional password remains mandatory even
        # for local callers that could fabricate gateway headers on the UDS.
        uid = request.headers.get('X-Trim-Userid', '')
        if request.headers.get('X-Trim-Isadmin') != 'true' or not uid.isdecimal():
            return jsonify(error='请通过飞牛管理员账户打开 NAS Terminal。'), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='不接受跨站请求。'), 403
        if request.method == 'POST' and (not request.is_json or request.headers.get('X-Dream-Terminal') != '1'):
            return jsonify(error='请求格式无效。'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; "
            "font-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
        return response

    @app.errorhandler(ValueError)
    def bad_input(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error='请求太大。'), 413

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith(PREFIX + '/api/'):
            messages = {400: '请求内容无法解析。', 404: '终端接口不存在。', 405: '请求方法不支持。'}
            return jsonify(error=messages.get(exc.code, '终端请求失败。'), code='HTTP_ERROR'), exc.code
        return exc

    @app.errorhandler(Exception)
    def server_error(exc):
        # Correlate failures without logging passwords, bodies or file contents.
        error_id = secrets.token_hex(4)
        app.logger.error('Terminal failure id=%s type=%s', error_id, type(exc).__name__)
        return jsonify(error='终端服务内部错误，请检查应用服务日志。错误编号：' + error_id,
                       code='INTERNAL_ERROR', error_id=error_id), 500

    @app.route(PREFIX)
    @app.route(PREFIX + '/')
    def index():
        return send_from_directory(assets, 'index.html')

    @app.get(PREFIX + '/static/<path:name>')
    def static(name):
        return send_from_directory(assets, name)

    @app.post(PREFIX + '/api/login')
    def login():
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('请求格式无效。')
        now = time.monotonic()
        with sessions.lock:
            sessions.sweep()
            sessions.failures = [t for t in sessions.failures if now - t < 60]
            if len(sessions.failures) >= 5:
                return jsonify(error='密码尝试过多，请一分钟后重试。'), 429
            try:
                record = json.loads((Path(state) / 'auth.json').read_text())
                if not isinstance(record, dict) or set(record) != {'salt', 'digest'}:
                    raise ValueError('invalid auth record')
                if len(bytes.fromhex(record['salt'])) != 16 or len(bytes.fromhex(record['digest'])) != 64:
                    raise ValueError('invalid auth record')
            except (OSError, ValueError, TypeError):
                app.logger.error('Terminal credential file unavailable or invalid')
                return jsonify(error='终端密码配置无法读取。请在飞牛应用设置中重新设置终端密码；若仍失败，请检查应用数据权限。',
                               code='AUTH_CONFIG_UNAVAILABLE'), 503
            if not verify_password(body.get('password'), record):
                sessions.failures.append(now)
                return jsonify(error='终端密码不正确。'), 401
            uid = request.headers['X-Trim-Userid']
            # A successful unlock does not reserve an SSH slot. Replace only
            # this user's unused unlock tokens, never another user's session
            # or an active SSH terminal. Failed passwords must not evict anyone.
            for previous, item in list(sessions.items.items()):
                if item['uid'] == uid and item['tty'] is None:
                    sessions.drop(previous)
            if len(sessions.items) >= MAX_AUTH_SESSIONS:
                return jsonify(error='解锁会话过多，请稍后再试。未连接会话会在两分钟内清理。'), 429
            token = secrets.token_urlsafe(32)
            sessions.items[token] = {'uid': uid, 'born': now, 'idle': now, 'tty': None}
            return jsonify(token=token, username=request.headers.get('X-Trim-Username', ''), ssh_port=config['ssh_port'],
                           **sessions.counts(uid))

    @app.post(PREFIX + '/api/<action>')
    def api(action):
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('请求格式无效。')
        token = request.headers.get('X-Dream-Session') or request.headers.get('Authorization', '').removeprefix('Bearer ')
        with sessions.lock:
            sessions.sweep()
            item = sessions.items.get(token)
            if not item or item['uid'] != request.headers['X-Trim-Userid']:
                return jsonify(error='会话已过期，请重新解锁终端。'), 401
            if action == 'logout':
                sessions.drop(token)
                return jsonify(ok=True)
            if action == 'sessions':
                return jsonify(**sessions.counts(item['uid']))
            if action == 'close_others':
                # Explicit recovery after both gateway and terminal-password
                # authentication. Never closes any other NAS user's sessions.
                count = 0
                for other, previous in list(sessions.items.items()):
                    if other != token and previous['uid'] == item['uid']:
                        sessions.drop(other)
                        count += 1
                # A connect response can be lost before the browser switches to
                # the terminal. Recover that connection too, retaining this
                # already-authenticated token for the next connection attempt.
                if item['tty']:
                    item['tty'].close()
                    item['tty'] = None
                    count += 1
                item['idle'] = time.monotonic()
                return jsonify(closed=count, **sessions.counts(item['uid']))
            if action == 'connect':
                if item['tty']:
                    return jsonify(error='已有终端连接，请先断开。'), 409
                if sessions.counts(item['uid'])['active_connections'] >= MAX_TERMINALS:
                    return jsonify(error='已有 4 个 SSH 连接。可以关闭本账号旧连接后重试；其他用户的连接不会被关闭。',
                                   code='TERMINAL_CAPACITY'), 409
                item['tty'] = sessions.factory(body.get('username'), state, config['ssh_port'],
                                               body.get('cols', 90), body.get('rows', 24))
                return jsonify(ok=True)
            tty = item['tty']
            if not tty:
                raise ValueError('请先连接 NAS。')
            if action == 'read':
                return jsonify(tty.read(body.get('offset', 0)))
            if action == 'write':
                tty.write(body.get('data'), body.get('seq'))
            elif action == 'resize':
                tty.resize(body.get('cols'), body.get('rows'))
            elif action == 'disconnect':
                tty.close()
                item['tty'] = None
                item['idle'] = time.monotonic()
            else:
                return jsonify(error='接口不存在。'), 404
            return jsonify(ok=True)

    return app


def main():
    from waitress import serve
    if os.geteuid() == 0:
        raise SystemExit('Refusing to run the terminal web service as root.')
    if int(os.environ.get('LISTEN_FDS', '0')) != 1 or int(os.environ.get('LISTEN_PID', '0')) != os.getpid():
        raise SystemExit('A systemd Unix socket is required; no public TCP listener is supported.')
    listener = socket.socket(fileno=3)
    if listener.family != socket.AF_UNIX:
        raise SystemExit('Only a Unix socket is permitted.')
    app = create_app(os.environ['DREAM_TERMINAL_STATE'])
    def janitor():
        while True:
            time.sleep(10)
            app.extensions['terminals'].sweep()
    threading.Thread(target=janitor, daemon=True).start()
    try:
        serve(app, sockets=[listener], threads=4, connection_limit=32, channel_timeout=30,
              max_request_body_size=16384, max_request_header_size=8192, expose_tracebacks=False)
    finally:
        app.extensions['terminals'].close()


if __name__ == '__main__':
    main()
