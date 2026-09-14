"""Authenticated web application; production entry point is Waitress in cli.py."""
from collections import OrderedDict
from datetime import timedelta
import functools
import hmac
import secrets
import threading
import time

from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from usb_link import usb_devices
from core import Monitor, Settings, check_password, validate_config


class LoginLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = OrderedDict()

    def admit(self, address):
        with self.lock:
            now = time.monotonic()
            count, start = self.attempts.pop(address, (0, now))
            if now - start >= 300:
                count, start = 0, now
            self.attempts[address] = (count + 1, start)
            while len(self.attempts) > 1024:
                self.attempts.popitem(last=False)
            return count < 5

    def clear(self, address):
        with self.lock:
            self.attempts.pop(address, None)


def create_app(directory, monitor=None, secure_cookie=False):
    settings = monitor.settings if monitor else Settings(directory)
    monitor = monitor or Monitor(settings)
    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.auth()['secret'], MAX_CONTENT_LENGTH=32768,
                      SESSION_COOKIE_NAME='nas_display_session', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=secure_cookie,
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    app.extensions.update(settings=settings, monitor=monitor)
    limits = LoginLimits()

    def logged_in():
        return (session.get('generation') == settings.auth()['generation']
                and time.time() - session.get('login_at', 0) < 43200)

    def private(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not logged_in():
                return jsonify(error='请先登录。'), 401
            return fn(*args, **kwargs)
        return wrapped

    @app.before_request
    def csrf():
        # Older Werkzeug versions do not consistently enforce this before JSON parsing.
        if request.content_length is not None and request.content_length > 32768:
            return jsonify(error='配置文件过大（上限 32 KB）。'), 413
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            expected = session.get('csrf', '')
            if not expected or not hmac.compare_digest(expected.encode(), request.headers.get('X-CSRF-Token', '').encode()):
                return jsonify(error='页面凭证已过期，请刷新后重试。'), 403
            if request.mimetype != 'application/json':
                return jsonify(error='请提交 JSON 数据。'), 415

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error={400: '请求格式错误。', 413: '配置文件过大（上限 32 KB）。',
                              404: '页面不存在。', 405: '请求方法不支持。'}.get(exc.code, '请求失败。')), exc.code

    @app.errorhandler(OSError)
    def file_error(exc):
        app.logger.error('Settings storage failure: %s', type(exc).__name__)
        return jsonify(error='配置保存失败，请检查服务的数据目录权限和剩余空间。'), 500

    def body(keys):
        data = request.get_json()
        if not isinstance(data, dict) or set(data) != set(keys):
            raise ValueError('请求字段不正确。')
        return data

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/api/session')
    def session_state():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return jsonify(authenticated=logged_in(), csrf=session['csrf'])

    @app.post('/api/login')
    def login():
        if not limits.admit(request.remote_addr):
            return jsonify(error='尝试次数过多，请 5 分钟后重试。'), 429
        auth = settings.auth()
        if not check_password(body(['password'])['password'], auth):
            return jsonify(error='密码不正确。'), 401
        limits.clear(request.remote_addr)
        session.clear()
        session.update(generation=auth['generation'], csrf=secrets.token_hex(32), login_at=time.time())
        session.permanent = True
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    @private
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/status')
    @private
    def status():
        return jsonify(monitor.status())

    @app.get('/api/devices')
    @private
    def devices():
        return jsonify(usb=usb_devices())

    @app.get('/api/fans')
    @private
    def fan_status():
        import fan_client
        return jsonify(fan_client.display_status(fan_client.status()))

    @app.post('/api/fans')
    @private
    def fan_set():
        import fan_client
        data = body(['channel', 'mode', 'speed_percent'])
        if not fan_client.valid_channel(data['channel']) or data['mode'] not in ('auto', 'manual'):
            raise ValueError('请选择已发现的风扇通道和自动 / 手动模式。')
        percent = data['speed_percent']
        if data['mode'] == 'manual' and (type(percent) is not int or not 0 <= percent <= 100):
            raise ValueError('风扇速度必须是 0–100 的整数百分比。')
        if data['mode'] == 'auto' and percent is not None:
            raise ValueError('自动模式无需设置速度百分比。')
        # Round halves upward: 0% -> 0, 50% -> 128, 100% -> 255.
        pwm = (percent * 255 + 50) // 100 if percent is not None else None
        try:
            result = fan_client.request({'action': 'set', 'channel': data['channel'], 'mode': data['mode'], 'pwm': pwm})
        except ValueError as exc:
            raise ValueError(fan_client.display_text(str(exc))) from None
        name = next((c.get('label') or c.get('fan') for c in result.get('channels', [])
                     if c.get('channel') == data['channel']), None) or '风扇'
        monitor.event(fan_client.display_text(name) + (' 已恢复自动模式' if data['mode'] == 'auto' else ' 已设为手动 ' + str(percent) + '%'))
        return jsonify(fan_client.display_status(result))

    @app.post('/api/fan-curve')
    @private
    def fan_curve_set():
        import fan_client
        from fan_curve import validate
        data = body(['channel', 'curve'])
        if not fan_client.valid_channel(data['channel']): raise ValueError('请选择已发现的风扇通道。')
        curve = validate(data['curve'])
        try:
            result = fan_client.request({'action':'curve', 'channel':data['channel'], 'curve':curve})
        except ValueError as exc:
            raise ValueError(fan_client.display_text(str(exc))) from None
        monitor.event('已应用风扇温度曲线。')
        return jsonify(fan_client.display_status(result))

    @app.get('/api/config')
    @private
    def configuration():
        return jsonify(settings.get())

    @app.post('/api/config')
    @private
    def save_configuration():
        cfg = validate_config(request.get_json())
        settings.save(cfg)
        monitor.event('配置已保存，发送已启用' if cfg['enabled'] else '配置已保存，发送已暂停')
        monitor.configuration_changed()
        return jsonify(ok=True)

    @app.post('/api/enabled')
    @private
    def enable():
        value = body(['enabled'])['enabled']
        with settings.lock:
            cfg = settings.get()
            cfg['enabled'] = value
            settings.save(cfg)
        monitor.event('发送已启用' if value else '发送已暂停')
        monitor.configuration_changed()
        return jsonify(ok=True)

    @app.post('/api/password')
    @private
    def password():
        data = body(['current', 'new'])
        settings.change_password(data['current'], data['new'])
        session.clear()  # Generation change also invalidates all other browser sessions.
        return jsonify(ok=True)

    return app
