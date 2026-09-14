"""Bounded, per-session PTY transport; only connects to NAS loopback SSH."""
import base64
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time

MAX_OUTPUT = 1024 * 1024


def dimensions(cols, rows):
    if type(cols) is not int or type(rows) is not int or not (20 <= cols <= 300 and 5 <= rows <= 150):
        raise ValueError('终端尺寸无效。')
    return cols, rows


def ssh_args(username, state, port):
    if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}', username):
        raise ValueError('NAS 用户名格式无效。')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('SSH 端口无效。')
    # No forwarded agents/keys, tunnel, user SSH configuration, or remote hosts.
    return ['-F', '/dev/null', '-tt', '-p', str(port), '-l', username,
            '-o', 'PreferredAuthentications=keyboard-interactive,password',
            '-o', 'PubkeyAuthentication=no', '-o', 'ForwardAgent=no',
            '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'PermitLocalCommand=no', '-o', 'EscapeChar=none',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'UserKnownHostsFile=' + str(Path(state) / 'known_hosts'),
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=3', '127.0.0.1']


class Terminal:
    def __init__(self, argv, home, cols=90, rows=24):
        dimensions(cols, rows)
        self.lock = threading.RLock()
        self.output = bytearray()
        self.total = 0
        self.closed = False
        self.eof = False
        self.input_seq = 0
        self.last_input = time.monotonic()
        self.master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home),
                   'TERM': 'xterm-256color', 'LANG': 'C.UTF-8'}
            self.process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                            close_fds=True, start_new_session=True, env=env, cwd=home)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @classmethod
    def ssh(cls, username, state, port, cols, rows):
        return cls([sys.executable, '-I', str(Path(__file__).with_name('pty_child.py')),
                    *ssh_args(username, state, port)], state, cols, rows)

    def _read(self):
        while True:
            with self.lock:
                if self.closed:
                    return
                try:
                    chunk = os.read(self.master, 16384)
                except BlockingIOError:
                    chunk = None
                except OSError:
                    chunk = b''
                if chunk == b'':
                    self.eof = True
                    self.process.poll()
                    return
                if chunk:
                    self.output.extend(chunk)
                    self.total += len(chunk)
                    if len(self.output) > MAX_OUTPUT:
                        del self.output[:-MAX_OUTPUT]
            time.sleep(.02)

    def read(self, offset):
        with self.lock:
            if type(offset) is not int or offset < 0 or offset > self.total:
                raise ValueError('输出游标无效。')
            first = self.total - len(self.output)
            start = max(offset, first)
            data = bytes(self.output[start - first:start - first + 32768])
            return {'data': base64.b64encode(data).decode(), 'offset': start + len(data),
                    'truncated': offset < first, 'ended': self.eof and start + len(data) == self.total,
                    'exit_code': self.process.poll()}

    def write(self, data, seq):
        if not isinstance(data, str) or type(seq) is not int:
            raise ValueError('输入格式无效。')
        raw = data.encode('utf-8')
        if not raw or len(raw) > 4096:
            raise ValueError('每次最多输入 4096 字节。')
        with self.lock:
            if seq == self.input_seq:
                return  # ACK lost: retry must never execute input twice.
            if seq != self.input_seq + 1:
                raise ValueError('输入顺序不一致，请重新连接。')
            if self.closed or self.eof:
                raise ValueError('终端已结束，请重新连接。')
            end = time.monotonic() + 2
            sent = 0
            try:
                while sent < len(raw):
                    try:
                        sent += os.write(self.master, raw[sent:])
                    except BlockingIOError:
                        if time.monotonic() > end:
                            raise OSError('TTY input timeout')
                        select.select([], [self.master], [], .05)
            except OSError:
                # Partial input is ambiguous; terminate, never replay it.
                self.close()
                raise ValueError('终端输入失败，连接已关闭。') from None
            self.input_seq = seq
            self.last_input = time.monotonic()

    def resize(self, cols, rows):
        dimensions(cols, rows)
        with self.lock:
            if not self.closed:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
                except ProcessLookupError:
                    self.process.wait(timeout=2)
            os.close(self.master)
            self.output.clear()
