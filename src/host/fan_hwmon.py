"""Driver-aware hwmon control; no probing, module loading or curve editing.

Only documented ITE/Nuvoton/Winbond PWM contracts are writable. Hardware paths
are discovered locally; clients send opaque IDs, never filesystem paths.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

ID = re.compile(r'hwmon-[0-9a-f]{16}-pwm[1-9][0-9]?\Z')
NCT = {'nct6106', 'nct6116', 'nct6775', 'nct6776', 'nct6779',
       'nct6791', 'nct6792', 'nct6793', 'nct6795', 'nct6796', 'nct6797', 'nct6798', 'nct6799'}
WINBOND = {'w83627ehf', 'w83627dhg', 'w83627uhg', 'w83667hg'}
ITE = {'it87', 'it8603', 'it8620', 'it8628', 'it8689', 'it8712', 'it8716',
       'it8718', 'it8720', 'it8721', 'it8728', 'it8732', 'it8771', 'it8772',
       'it8781', 'it8782', 'it8783', 'it8786', 'it8790', 'it8792', 'it87952'}


def integer(path, optional=False):
    try:
        text = path.read_text().strip()
        if not re.fullmatch(r'-?[0-9]+', text): raise ValueError('控制器读数无效。')
        return int(text)
    except OSError:
        if optional: return None
        raise


def profile(name):
    if name in NCT: return 'nct', (2, 3, 4, 5)
    if name in WINBOND: return 'winbond', (2, 3, 4, 5)
    if name in ITE: return 'ite', (2,)
    return None, ()


class Hardware:
    def __init__(self, sys=Path('/sys'), boot=None):
        self.sys = Path(sys)
        self.boot = boot or Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def discover(self):
        result, ambiguous = {}, set()
        for link in sorted((self.sys / 'class/hwmon').glob('hwmon*')):
            try:
                h = link.resolve(strict=True)
                # Never follow a class entry outside the kernel device tree.
                h.relative_to((self.sys / 'devices').resolve())
                driver = (h / 'name').read_text().strip()
                base = h.parent.parent if h.parent.name == 'hwmon' else h.parent
                key = hashlib.sha256((str(base) + ':' + driver).encode()).hexdigest()[:16]
                info = h.stat()
                for node in sorted(h.glob('pwm*')):
                    if not re.fullmatch(r'pwm[1-9][0-9]?', node.name): continue
                    channel = 'hwmon-' + key + '-' + node.name
                    if channel in ambiguous: continue
                    if channel in result:
                        result.pop(channel); ambiguous.add(channel); continue
                    result[channel] = {'path': h, 'node': node.name, 'driver': driver,
                                       'identity': {'boot': self.boot, 'device': str(base),
                                                    'inode': info.st_ino, 'driver': driver}}
            except (OSError, ValueError):
                continue
        if len(result) > 64: raise ValueError('控制通道数量超出本版上限（64）。')
        return result

    def read(self, channel):
        if not isinstance(channel, str) or not ID.fullmatch(channel): raise ValueError('无效控制通道。')
        item = self.discover().get(channel)
        if item is None: raise ValueError('控制器已断开或设备标识已改变。')
        h, node, driver = item['path'], item['node'], item['driver']
        family, auto_modes = profile(driver)
        mode, pwm = integer(h / (node + '_enable'), True), integer(h / node, True)
        fan = 'fan' + node[3:]
        label = (h / (fan + '_label')).read_text().strip() if (h / (fan + '_label')).exists() else fan
        settings = {}
        for p in sorted(h.glob(node + '_*')):
            if p.name in (node + '_enable', node + '_auto_start'): continue
            # Fixed kernel attribute names only. Snapshot stable configuration,
            # including DC/PWM mode; no unrelated temperature measurements.
            if re.fullmatch(node + r'_[a-z0-9_]+', p.name): settings[p.name] = integer(p)
        # Speed Cruise uses fanN attributes as well as pwmN attributes.
        for suffix in ('target', 'tolerance'):
            p = h / (fan + '_' + suffix)
            if p.exists(): settings[p.name] = integer(p)
        alias = integer(h / (node + '_auto_start'), True) if family == 'ite' else None
        reason = ''
        if family is None: reason = '此驱动暂未适配调速，保留转速读取。'
        elif pwm is None or not 0 <= pwm <= 255 or mode not in (0, 1, *auto_modes):
            reason = '缺少有效 PWM/模式接口，或当前处于全速/未支持的模式。'
        elif not all(p.stat().st_mode & 0o222 for p in (h / node, h / (node + '_enable'))):
            reason = '驱动仅提供只读控制接口。'
        elif family == 'ite' and mode == 2 and alias is None and not any('_auto_point' in k for k in settings):
            reason = 'ITE 自动控制参数未暴露，无法保存和恢复原自动设置。'
        elif alias is not None and not 0 <= alias <= 255:
            reason = 'ITE 自动起始 PWM 无效。'
        rpm = integer(h / (fan + '_input'), True)
        fault = integer(h / (fan + '_fault'), True)
        enabled = integer(h / (fan + '_enable'), True)
        return {**item, 'family': family, 'auto_modes': auto_modes, 'mode_raw': mode,
                'pwm': pwm, 'alias': alias, 'settings': settings, 'reason': reason,
                'rpm': rpm if rpm is not None and rpm >= 0 and fault != 1 and enabled != 0 else None,
                'alarm': integer(h / (fan + '_alarm'), True), 'fan': fan, 'label': label}

    def write(self, channel, field, value, identity):
        d = self.read(channel)
        if d['reason'] or d['identity'] != identity: raise ValueError('控制器能力或身份已改变，停止写入。')
        if field not in ('pwm', 'enable', 'auto_start') or type(value) is not int:
            raise ValueError('禁止写入该节点。')
        if field == 'enable':
            if value not in (1, *d['auto_modes']): raise ValueError('未支持的风扇模式。')
        elif not 0 <= value <= 255: raise ValueError('PWM 超出范围。')
        if field == 'auto_start' and d['alias'] is None: raise ValueError('非 ITE 起始参数接口。')
        directory = os.open(d['path'], os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(directory).st_ino != identity['inode']: raise ValueError('控制器已重载。')
            name = d['node'] if field == 'pwm' else d['node'] + '_' + field
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                value = (str(value) + '\n').encode()
                if os.write(fd, value) != len(value): raise OSError('Short write')
            finally: os.close(fd)
        finally: os.close(directory)


class Controller:
    def __init__(self, hardware, path, atomic, sleep=time.sleep):
        self.hw, self.path, self.atomic, self.sleep = hardware, path, atomic, sleep
        self.entries, self.errors = {}, {}
        if path.exists():
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or set(data) != {'boot', 'entries'} or not isinstance(data['entries'], dict):
                raise ValueError('通用风扇恢复记录损坏。')
            if data['boot'] != hardware.boot:
                atomic(path.with_name('hwmon-previous-boot.json'), data); self.save()
            else:
                self.entries = data['entries']
                for ch, e in self.entries.items():
                    if not ID.fullmatch(ch) or not isinstance(e, dict) or set(e) != {'identity', 'mode', 'pwm', 'alias', 'settings'}:
                        raise ValueError('通用风扇恢复记录损坏。')
                    if type(e['mode']) is not int or e['mode'] not in (1,2,3,4,5) or type(e['pwm']) is not int or not 0 <= e['pwm'] <= 255:
                        raise ValueError('保存的模式或 PWM 无效。')
                    if e['alias'] is not None and (type(e['alias']) is not int or not 0 <= e['alias'] <= 255):
                        raise ValueError('保存的起始 PWM 无效。')
                    if not isinstance(e['settings'], dict) or not isinstance(e['identity'], dict): raise ValueError('保存的控制器参数无效。')
                    if set(e['identity']) != {'boot', 'device', 'inode', 'driver'} or type(e['identity']['inode']) is not int:
                        raise ValueError('保存的控制器身份无效。')
                    if any(type(v) is not int for v in e['settings'].values()): raise ValueError('保存的自动参数无效。')

    def save(self): self.atomic(self.path, {'boot': self.hw.boot, 'entries': self.entries})

    def consistent(self, channel):
        d, e = self.hw.read(channel), self.entries[channel]
        if d['reason'] or d['identity'] != e['identity'] or d['settings'] != e['settings']:
            raise ValueError('控制器、驱动或自动参数已变化，停止覆盖；恢复记录已保留。')
        return d, e

    @staticmethod
    def manual(d):
        return d['mode_raw'] == 1 or (d['family'] == 'ite' and d['mode_raw'] == 0 and d['pwm'] == 255)

    def restore(self, channel):
        d, e = self.consistent(channel)
        if not self.manual(d) and d['mode_raw'] != e['mode']: raise ValueError('模式已被其他程序改变。')
        if e['alias'] is not None:
            if not self.manual(d) and d['alias'] != e['alias']: raise ValueError('自动起始值已被其他程序改变。')
            if self.manual(d): self.hw.write(channel, 'auto_start', e['alias'], e['identity'])
        if e['mode'] == 1: self.hw.write(channel, 'pwm', e['pwm'], e['identity'])
        self.sleep(1.6)
        check, _ = self.consistent(channel)
        if e['alias'] is not None and check['alias'] != e['alias']: raise ValueError('起始 PWM 恢复校验失败。')
        if e['mode'] == 1 and check['pwm'] != e['pwm']: raise ValueError('原手动 PWM 恢复校验失败。')
        if check['mode_raw'] != e['mode']: self.hw.write(channel, 'enable', e['mode'], e['identity'])
        self.sleep(1.6)
        check, _ = self.consistent(channel)
        if check['mode_raw'] != e['mode']: raise ValueError('原模式恢复校验失败。')
        del self.entries[channel]
        try: self.save()
        except OSError: self.entries[channel] = e; raise
        self.errors.pop(channel, None)

    def recover(self):
        for channel in list(self.entries):
            try: self.restore(channel)
            except (OSError, ValueError) as exc: self.errors[channel] = str(exc)

    def apply(self, channel, mode, pwm):
        d = self.hw.read(channel)
        if d['reason']: raise ValueError(d['reason'])
        if mode == 'auto':
            if channel in self.entries:
                if self.entries[channel]['mode'] == 1: raise ValueError('接管前已是手动，未记录自动模式，不能猜测自动策略。')
                try: self.restore(channel)
                except (OSError, ValueError) as exc: self.errors[channel] = str(exc); raise
            elif d['mode_raw'] not in d['auto_modes']: raise ValueError('未记录原自动模式。')
            return
        if mode != 'manual' or type(pwm) is not int or not 0 <= pwm <= 255: raise ValueError('PWM 必须是 0–255 的整数。')
        if channel in self.errors: raise ValueError('通道仍有恢复错误，请先恢复原模式或检查服务日志。')
        if channel not in self.entries:
            if d['mode_raw'] not in (1, *d['auto_modes']): raise ValueError('当前为全速/未接管模式，请先在 BIOS 设置原自动策略。')
            if d['alias'] is not None and d['mode_raw'] == 2 and d['alias'] != d['pwm']:
                raise ValueError('ITE 自动起始寄存器不一致。')
            self.entries[channel] = {'identity': d['identity'], 'mode': d['mode_raw'], 'pwm': d['pwm'],
                                     'alias': d['alias'], 'settings': d['settings']}
            try: self.save()
            except BaseException: del self.entries[channel]; raise
        d, e = self.consistent(channel)
        if not self.manual(d) and d['mode_raw'] != e['mode']: raise ValueError('模式已被其他程序改变。')
        if not self.manual(d) and e['alias'] is not None and d['alias'] != e['alias']: raise ValueError('自动起始值已改变。')
        try:
            if d['mode_raw'] != 1: self.hw.write(channel, 'enable', 1, e['identity'])
            self.hw.write(channel, 'pwm', pwm, e['identity']); self.sleep(1.6)
            check, _ = self.consistent(channel)
            if check['pwm'] != pwm or not self.manual(check): raise ValueError('手动模式/PWM 校验失败。')
        except (OSError, ValueError):
            try: self.restore(channel)
            except (OSError, ValueError): self.errors[channel] = '设置失败且原模式未恢复，请检查风扇；恢复记录已保留。'
            raise ValueError(self.errors.get(channel) or '设置失败，已恢复接管前模式。') from None

    def status(self, exclude=()):
        channels = []
        for channel, item in self.hw.discover().items():
            if (item['driver'], item['node']) in exclude: continue
            try:
                d = self.hw.read(channel); e = self.entries.get(channel)
                auto = d['mode_raw'] in d['auto_modes']
                can_auto = (e['mode'] in d['auto_modes']) if e else auto
                note = d['reason'] or ('恢复接管前的自动策略（模式 ' + str(e['mode'] if e else d['mode_raw']) + '）。' if can_auto else
                                      '接管前为手动；可设 PWM，不能猜测自动策略。')
                if d['alias'] is not None and auto: note += ' 自动时寄存器为起始 PWM，不是实时占空比。'
                channels.append({'channel': channel, 'label': d['driver'] + ' · ' + d['label'] + ' / ' + d['node'],
                                 'driver': d['driver'], 'fan': d['fan'],
                                 'mode': 'auto' if auto else 'manual' if self.manual(d) and d['family'] else 'unknown',
                                 'pwm': None if auto else d['pwm'], 'register': d['pwm'], 'rpm': d['rpm'],
                                 'alarm': d['alarm'], 'temperature_c': None,
                                 'can_set': not bool(d['reason']) and (d['mode_raw'] in (1, *d['auto_modes']) or bool(e)),
                                 'can_auto': can_auto, 'note': self.errors.get(channel) or note + ' 转速为同编号反馈，物理接线需确认。'})
            except (OSError, ValueError) as exc:
                channels.append({'channel': channel, 'label': item['driver'] + ' / ' + item['node'],
                                 'can_set': False, 'can_auto': False, 'rpm': None, 'note': '读取失败：' + str(exc)})
        return channels


class Broker:
    def __init__(self, legacy, atomic):
        self.legacy = legacy
        self.common = Controller(Hardware(), legacy.path.with_name('hwmon-active.json'), atomic)

    @property
    def error(self): return self.legacy.error or '；'.join(self.common.errors.values()) or None

    def recover(self): self.legacy.recover(); self.common.recover()

    def status(self):
        old = self.legacy.status()
        channels = old['channels'] if old['available'] else []
        extra = self.common.status({('it8613', c['channel']) for c in channels})
        return {'available': bool(channels or extra), 'error': self.error,
                'channels': channels + extra, 'note': None if channels or extra else
                '内核未暴露 PWM 控制通道；已有 EC/WMI 风扇读数仍可显示。需匹配主板芯片的内核驱动，不能仅凭品牌开启调速。'}

    def apply(self, channel, mode, pwm):
        if channel in ('pwm2', 'pwm3'): self.legacy.apply(channel, mode, pwm)
        else: self.common.apply(channel, mode, pwm)
        return self.status()
