"""Native ESP32-S3 USB link, including device replacement and bounded recovery."""
import errno
import json
import os
from pathlib import Path
import termios
import time
import serial
from serial.tools import list_ports


class TransferCancelled(Exception):
    """A configuration change or shutdown superseded this transfer."""


def usb_devices():
    devices = []
    for port in list_ports.comports():
        if (port.vid, port.pid) != (0x303a, 0x1001):
            continue
        stable = next((str(p) for p in sorted(Path('/dev/serial/by-id').glob('*'))
                       if p.resolve() == Path(port.device).resolve()), port.device)
        devices.append({'path': stable, 'label': f'ESP32-S3 · {port.serial_number or port.device}'})
    return sorted(devices, key=lambda d: d['path'])


def device_identity(path):
    stat = os.stat(path)
    return stat.st_dev, stat.st_ino, stat.st_rdev



class UsbLink:
    STARTUP_WAIT = 4.0

    def __init__(self):
        self.serial = None
        self.path = None
        self.identity = None
        self.sequence = 0
        self.buffer = bytearray()
        self.next_attempt = 0

    def close(self):
        # Forget the old descriptor even if unplugging makes cleanup raise an error.
        port, self.serial = self.serial, None
        self.path = self.identity = None
        self.buffer.clear()
        if port is not None:
            try:
                # Cancel queued writes before close; Linux otherwise can drain a dead
                # CDC endpoint for tens of seconds, stalling reconnect and shutdown.
                port.reset_output_buffer()
            except (OSError, termios.error, serial.SerialException):
                pass
            finally:
                try:
                    port.close()
                except (OSError, termios.error, serial.SerialException):
                    pass

    @staticmethod
    def check_cancel(cancelled):
        if cancelled is not None and cancelled():
            raise TransferCancelled()

    def messages(self, chunk):
        for value in chunk:
            if value == 10:
                try:
                    data = json.loads(self.buffer)
                except (ValueError, UnicodeError):
                    data = None
                self.buffer.clear()
                if isinstance(data, dict):
                    yield data
            elif len(self.buffer) < 2048:
                self.buffer.append(value)

    def wait_for_startup(self, cancelled=None):
        # A ready line can belong to the previous boot buffered before a CDC reset.
        # Always give a newly opened device the full grace period before writing.
        deadline = time.monotonic() + self.STARTUP_WAIT
        while time.monotonic() < deadline:
            self.check_cancel(cancelled)
            for _ in self.messages(self.serial.read(512)):
                pass
        self.buffer.clear()

    def response(self, kind, timeout, sequence=None, cancelled=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check_cancel(cancelled)
            for data in self.messages(self.serial.read(512)):
                if data.get('kind') == kind and data.get('v') == 1:
                    if sequence is None or data.get('seq') == sequence:
                        return
        raise ValueError('USB 未收到屏幕确认；将自动重试，请检查固件和数据线。')

    def open(self, selected, cancelled=None):
        self.check_cancel(cancelled)
        devices = usb_devices()
        if selected == 'auto':
            if len(devices) != 1:
                self.close()
                raise ValueError('未找到唯一的 ESP32-S3 USB 设备；请连接数据线，或在网页中选择设备。')
            selected = devices[0]['path']
        elif selected not in [d['path'] for d in devices]:
            self.close()
            raise ValueError('选择的 USB 设备未连接，重新接入后会自动恢复。')
        identity = device_identity(selected)
        if self.serial is not None and self.path == selected and self.identity == identity:
            return
        self.close()
        port = serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=.5, exclusive=True)
        # The native USB/JTAG controller treats deasserting DTR as a reset
        # transition on this board. Keep DTR asserted and RTS deasserted.
        port.dtr = True
        port.rts = False
        port.port = selected
        self.serial, self.path = port, selected
        port.open()
        # Do not ask the tty driver to hang up/reset the board on close.
        attributes = termios.tcgetattr(port.fileno())
        attributes[2] &= ~termios.HUPCL
        termios.tcsetattr(port.fileno(), termios.TCSANOW, attributes)
        stat = os.fstat(port.fileno())
        self.identity = stat.st_dev, stat.st_ino, stat.st_rdev
        port.reset_input_buffer()
        self.wait_for_startup(cancelled)
        self.check_cancel(cancelled)
        hello = b'\nNAS_DISPLAY_HELLO\n'
        if port.write(hello) != len(hello):
            raise serial.SerialTimeoutException('Incomplete handshake')
        self.response('nas-display-ready', 1.5, cancelled=cancelled)

    def send(self, sample_bytes, selected, cancelled=None):
        self.check_cancel(cancelled)
        # A skipped retry must not move the deadline forward indefinitely.
        if time.monotonic() < self.next_attempt:
            raise ValueError('USB 正在重新连接，请稍候。')
        try:
            self.open(selected, cancelled)
            self.check_cancel(cancelled)
            data = json.loads(sample_bytes() if callable(sample_bytes) else sample_bytes)
            data.pop('token', None)
            self.sequence = (self.sequence + 1) % 2**31
            data.update(kind='nas-display-sample', seq=self.sequence)
            payload = json.dumps(data, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
            if self.serial.write(payload) != len(payload):
                raise serial.SerialTimeoutException('Incomplete write')
            self.response('nas-display-ack', .5, self.sequence, cancelled)
        except TransferCancelled:
            self.close()
            raise
        except serial.SerialTimeoutException:
            self.close()
            self.next_attempt = time.monotonic() + 2
            raise ValueError('USB 写入超时，正在重新连接；若持续失败，请检查数据线和串口占用。') from None
        except (OSError, termios.error, ValueError, serial.SerialException) as exc:
            self.close()
            self.next_attempt = time.monotonic() + 2
            if isinstance(exc, ValueError):
                raise
            if getattr(exc, 'errno', None) == errno.EACCES:
                raise ValueError('USB 权限不足，请检查服务账号的串口设备组。') from None
            raise ValueError('USB 已断开或被其他程序占用，正在等待重新连接。') from None
