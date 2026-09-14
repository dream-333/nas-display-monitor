"""Read i915 engine busy counters through perf_event_open (no external tool).

Counter names/configurations come from sysfs, not a CPU model table. This
backend intentionally does not apply the i915 ABI to the separate xe driver.
"""
import atexit
import ctypes
import errno
import os
from pathlib import Path
import platform
import re
import struct
import threading
import time


def text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def open_counter(kind, config, cpu):
    # perf_event_attr VER0, no sampling; read value + enabled/running times.
    syscall = {'x86_64': 298, 'aarch64': 241}.get(platform.machine())
    if syscall is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError(errno.ENOSYS, 'Unsupported perf syscall architecture')
    attr = ctypes.create_string_buffer(64)
    struct.pack_into('=IIQQQQQ', attr, 0, kind, 64, config, 0, 0, 3, 0)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    fd = libc.syscall(ctypes.c_long(syscall), ctypes.byref(attr), ctypes.c_int(-1),
                      ctypes.c_int(cpu), ctypes.c_int(-1), ctypes.c_ulong(8))  # CLOEXEC
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return fd


def read_counter(fd):
    data = os.read(fd, 24)
    if len(data) != 24:
        raise OSError(errno.EIO, 'Short perf counter read')
    return struct.unpack('=QQQ', data)


def percentage(previous, current):
    busy, enabled, running = (b - a for a, b in zip(previous, current))
    if busy < 0 or enabled <= 0 or running <= 0 or running > enabled:
        return None
    # Both busy time and perf running time use ns. Correct for multiplexing.
    return round(min(100.0, 100.0 * busy / running), 2)


class IntelPMU:
    def __init__(self, root=Path('/sys/bus/event_source/devices'),
                 online=Path('/sys/devices/system/cpu/online'),
                 opener=open_counter, reader=read_counter, closer=os.close,
                 clock=time.monotonic):
        self.root, self.online = root, online
        self.opener, self.reader, self.closer, self.clock = opener, reader, closer, clock
        self.states = {}
        self.lock = threading.Lock()

    def _close(self, state):
        for fd in state.get('fds', {}).values():
            try:
                self.closer(fd)
            except OSError:
                pass
        state['fds'] = {}

    def close(self):
        with self.lock:
            for state in self.states.values():
                self._close(state)
            self.states.clear()

    def mapping(self, devices):
        result = {}
        # Discrete devices publish PCI-qualified names; integrated i915 uses
        # the legacy unqualified name. Never share it across ambiguous GPUs.
        unmatched = []
        for gpu in devices:
            if gpu['driver'] != 'i915':
                continue
            address = gpu['device']
            path = self.root / ('i915_' + address.replace(':', '_'))
            if path.is_dir():
                result[address] = path
            else:
                unmatched.append(address)
        if len(unmatched) == 1 and (self.root / 'i915').is_dir():
            result[unmatched[0]] = self.root / 'i915'
        return result

    def sample(self, devices):
        with self.lock:
            mapping = self.mapping(devices)
            for address in list(self.states):
                if address not in mapping:
                    self._close(self.states.pop(address))
            result = {}
            for gpu in devices:
                if gpu['driver'] != 'i915':
                    continue
                address = gpu['device']
                result[address] = self._sample(address, mapping.get(address))
            return result

    def _sample(self, address, path):
        missing = {'busy_percent': None, 'busy_source': 'i915 PMU',
                   'busy_status': 'not_exposed', 'engine_busy_percent': {}}
        if path is None:
            return missing
        now = self.clock()
        state = self.states.setdefault(address, {'fds': {}})
        try:
            stat = path.stat()
            identity = (str(path.resolve()), stat.st_dev, stat.st_ino, text(path / 'type'))
            if state.get('identity') != identity:
                self._close(state)
                state.clear()
                state.update(identity=identity, fds={})
            if now < state.get('retry_at', 0):
                return state['result']
            if not state['fds']:
                kind = int(text(path / 'type'))
                cpu_list = text(path / 'cpumask') or text(self.online)
                cpu = int(re.split('[-,]', cpu_list)[0])
                configs = {}
                for event in sorted((path / 'events').glob('*-busy')):
                    match = re.fullmatch(r'config=(0x[0-9a-fA-F]+|[0-9]+)', text(event))
                    unit = text(event.with_name(event.name + '.unit'))
                    if not match or unit not in ('', 'ns'):
                        continue
                    configs[event.name[:-5]] = int(match[1], 16 if match[1].startswith('0x') else 10)
                if not configs:
                    state.update(result=missing, retry_at=now + 10)
                    return missing
                for engine, config in configs.items():
                    state['fds'][engine] = self.opener(kind, config, cpu)
                state['previous'] = {engine: self.reader(fd) for engine, fd in state['fds'].items()}
                return dict(missing, busy_status='warming_up')
            current = {engine: self.reader(fd) for engine, fd in state['fds'].items()}
            engines = {engine: percentage(state['previous'][engine], value) for engine, value in current.items()}
            state['previous'] = current
            # Do not silently drop a failed/stalled counter and report false idle.
            valid = all(value is not None for value in engines.values())
            return dict(missing, busy_percent=max(engines.values()) if valid else None,
                        engine_busy_percent=engines, busy_status='ok' if valid else 'warming_up')
        except (OSError, ValueError, OverflowError) as exc:
            self._close(state)
            status = 'permission_denied' if getattr(exc, 'errno', None) in (errno.EACCES, errno.EPERM) else 'unavailable'
            value = dict(missing, busy_status=status, busy_error=str(exc))
            state.update(result=value, retry_at=now + 10)
            return value


monitor = IntelPMU()
atexit.register(monitor.close)
