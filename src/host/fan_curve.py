"""Software fan curves. Root broker owns sampling, writes and recovery.

Applied curves persist across service starts; stop restores the hardware baseline.
No paths or temperature values come from clients.
"""
import copy
import json
import math
import re
import time
from pathlib import Path
from fan_client import valid_channel

PRESETS = {
    'silent': {'name': '静音', 'cpu': [[35,40],[55,45],[70,65],[85,100]], 'board': [[25,40],[35,45],[45,65],[60,100]], 'step_up':2, 'step_down':15},
    'standard': {'name': '标准', 'cpu': [[35,40],[50,55],[65,75],[80,100]], 'board': [[25,40],[35,55],[45,75],[55,100]], 'step_up':1, 'step_down':10},
    'performance': {'name': '性能', 'cpu': [[30,60],[45,75],[60,90],[75,100]], 'board': [[25,60],[30,75],[40,90],[50,100]], 'step_up':0, 'step_down':5},
    'full': {'name': '全速', 'cpu': [[30,100],[85,100]], 'board': [[25,100],[60,100]], 'step_up':0, 'step_down':0},
}
KEYS = {'preset','source','points','interpolation','hysteresis','step_up','step_down','min_percent','critical_temp'}


def finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def validate(config):
    if not isinstance(config, dict) or set(config) != KEYS:
        raise ValueError('曲线配置字段不完整或包含未知字段。')
    c = copy.deepcopy(config)
    if c['preset'] not in (*PRESETS, 'custom') or c['interpolation'] not in ('linear', 'step'):
        raise ValueError('请选择有效的曲线预设和线性 / 阶梯模式。')
    if not isinstance(c['source'], str) or not (c['source']=='cpu:auto' or re.fullmatch(r'(cpu|board)-[a-f0-9]{24}',c['source'])):
        raise ValueError('请选择本机已发现的温度源。')
    points=c['points']
    if not isinstance(points,list) or not 2 <= len(points) <= 8:
        raise ValueError('曲线需要 2–8 个温度节点。')
    for p in points:
        if not isinstance(p,list) or len(p)!=2 or not finite(p[0],0,100) or type(p[1]) is not int or not 0<=p[1]<=100:
            raise ValueError('节点温度需为 0–100°C，速度需为 0–100 的整数。')
    if any(b[0]<=a[0] or b[1]<a[1] for a,b in zip(points,points[1:])) or points[-1][1]!=100:
        raise ValueError('温度必须递增，速度不能随温度下降，最后一个节点须为 100%。')
    for k,lo,hi in [('hysteresis',0,10),('step_up',0,10),('step_down',0,60),('critical_temp',40,100)]:
        if not finite(c[k],lo,hi): raise ValueError('回差、延迟或保护温度超出允许范围。')
    if type(c['min_percent']) is not int or not 0<=c['min_percent']<=100:
        raise ValueError('曲线最低输出需为 0–100% 的整数。')
    return c


def preset(name='standard', source='cpu:auto', kind='cpu'):
    p=PRESETS[name]
    return dict(preset=name,source=source,points=copy.deepcopy(p[kind]),interpolation='linear',
                hysteresis=3,step_up=p['step_up'],step_down=p['step_down'],min_percent=40,
                critical_temp=85 if kind=='cpu' else 60)


def target(config, temperature):
    if temperature>=config['critical_temp']: return 100
    points=config['points']; speed=points[0][1]
    for a,b in zip(points,points[1:]):
        if temperature < a[0]: break
        if temperature>=b[0]: speed=b[1];continue
        speed=a[1] if config['interpolation']=='step' else a[1]+(b[1]-a[1])*(temperature-a[0])/(b[0]-a[0])
        break
    return max(config['min_percent'], min(100, int(speed+.5)))


def sensors():
    import collect
    from board import board_stats
    sample=collect.temperatures(); chosen=collect.select_cpu_temperature(sample)
    result=[{'id':'cpu:auto','label':'CPU · 自动（封装 / Tdie / Tctl）','kind':'cpu','temperature_c':chosen['value_c'],
             'identity': sorted(s.get('id',s.get('path','')) for s in sample['cpu_temperature_sources'])}]
    result.extend({'id':s['id'],'label':s['driver']+' / '+s['label'],'kind':'cpu','temperature_c':s['value_c']}
                  for s in sample['cpu_temperature_candidates'])
    result.extend({'id':s['id'],'label':s['driver']+' / '+s['label'],'kind':'board','temperature_c':s['temperature_c']}
                  for s in board_stats())
    return result


class Engine:
    def __init__(self, broker, path, atomic, reader=sensors, clock=time.monotonic):
        self.broker,self.path,self.atomic,self.reader,self.clock=broker,Path(path),atomic,reader,clock
        self.profiles,self.active,self.errors,self.enabled={},{},{},{}
        if self.path.exists():
            data=json.loads(self.path.read_text())
            if isinstance(data,dict) and 'version' in data:
                if set(data)!={'version','profiles','enabled'} or data['version']!=2:
                    raise ValueError('已保存的曲线配置版本无效。')
                enabled=data['enabled'];data=data['profiles']
                if not isinstance(enabled,dict) or len(enabled)>8:
                    raise ValueError('已启用的曲线记录无效。')
                for ch,identity in enabled.items():
                    valid_identity=(isinstance(identity,str) and 0<len(identity)<=256 or
                                    isinstance(identity,list) and 0<len(identity)<=64 and
                                    all(isinstance(v,str) and 0<len(v)<=256 for v in identity))
                    if not valid_channel(ch) or not valid_identity:
                        raise ValueError('已启用的曲线温度源记录无效。')
                self.enabled=copy.deepcopy(enabled)
            if not isinstance(data,dict) or len(data)>64: raise ValueError('已保存的曲线配置无效。')
            for ch,c in data.items():
                if not valid_channel(ch): raise ValueError('已保存的曲线通道无效。')
                self.profiles[ch]=validate(c)
            if not self.enabled.keys()<=self.profiles.keys():
                raise ValueError('已启用的曲线缺少配置。')

    def save(self, profiles, enabled):
        self.atomic(self.path,{'version':2,'profiles':profiles,'enabled':enabled})
        self.profiles,self.enabled=copy.deepcopy(profiles),copy.deepcopy(enabled)

    def disable(self, channel):
        if channel in self.enabled:
            enabled=dict(self.enabled);enabled.pop(channel)
            self.save(self.profiles,enabled)

    def resume(self):
        # 1.5.0 draft-only files have no enabled entries; never guess their intent.
        for ch,identity in list(self.enabled.items()):
            try: self.start(ch,self.profiles[ch],expected_identity=identity)
            except (OSError,ValueError) as exc:
                # Startup recovery already restored the baseline. Do not write to
                # a missing/replaced channel merely because an old profile exists.
                self.active.pop(ch,None)
                message='曲线自动恢复失败：'+(str(exc) if isinstance(exc,ValueError) else '读写失败。')
                try: self.disable(ch)
                except OSError: message+=' 无法保存停用状态，请检查配置存储。'
                self.errors[ch]=message

    @property
    def error(self): return self.broker.error or '；'.join(self.errors.values()) or None

    def source(self, source):
        matches=[s for s in self.reader() if s['id']==source]
        if len(matches)!=1 or not finite(matches[0]['temperature_c'],0,125):
            raise ValueError('曲线温度源失效，已停止软件曲线。')
        return matches[0]

    def status(self):
        result=self.broker.status()
        result['error']=self.error
        result['curve_sources']=self.reader()
        result['curve_presets']=copy.deepcopy(PRESETS)
        for row in result['channels']:
            ch=row['channel'];r=self.active.get(ch)
            row['curve']=copy.deepcopy(self.profiles.get(ch))
            row['curve_active']=bool(r)
            row['curve_enabled']=ch in self.enabled
            row['curve_error']=self.errors.get(ch)
            row['can_curve']=row.get('can_set',False) and row.get('can_auto',row.get('mode')=='auto')
            if r:
                row.update(mode='curve',curve_temperature=r['temperature'],curve_target=r['target'],
                           curve_state=r['state'],note='后台按温度曲线调节；关闭网页继续运行，主板自动可退出曲线。转速为该通道反馈，物理接线需确认。')
        return result

    def start(self, channel, config, *, expected_identity=None):
        if not valid_channel(channel): raise ValueError('无效风扇通道。')
        c=validate(config)
        if channel not in self.active and len(self.active)>=8: raise ValueError('最多同时运行 8 条风扇曲线。')
        row=next((r for r in self.broker.status()['channels'] if r['channel']==channel),{})
        if not row.get('can_set') or not row.get('can_auto',row.get('mode')=='auto'):
            raise ValueError('此通道不能恢复原自动策略，暂不支持软件曲线。')
        sensor=self.source(c['source']);temp=sensor['temperature_c']
        identity=copy.deepcopy(sensor.get('identity',sensor['id']))
        if expected_identity is not None and identity!=expected_identity:
            raise ValueError('温度源已改变，请重新选择并应用曲线。')
        profiles=dict(self.profiles);profiles[channel]=c
        if len(profiles)>64: raise ValueError('已保存曲线超过 64 条。')
        enabled=dict(self.enabled);enabled[channel]=identity
        self.save(profiles,enabled)  # Persistent failure must precede any write.
        self.active.pop(channel,None)
        speed=target(c,temp)
        try: self.broker.apply(channel,'manual',(speed*255+50)//100)
        except (OSError,ValueError):
            self.fail(channel,'曲线启动失败。')
            raise
        now=self.clock()
        self.active[channel]={'last':now,'temperature':temp,'filtered':temp,'speed':speed,'target':speed,
                              'sensor_identity':identity,
                              'direction':0,'pending':now,'zero_since':None,'state':'曲线停转（0%）' if speed==0 else '运行中'}
        self.errors.pop(channel,None)
        return self.status()

    def apply(self, channel, mode, pwm):
        # Explicit fixed/auto request cancels software control even if restoration fails.
        self.disable(channel)
        self.active.pop(channel,None)
        result=self.broker.apply(channel,mode,pwm)
        self.errors.pop(channel,None)
        return self.status()

    def fail(self,channel,message):
        self.active.pop(channel,None)
        try: self.disable(channel)
        except OSError: message+=' 无法保存停用状态，请检查配置存储。'
        try:
            self.broker.apply(channel,'auto',None)
            message+=' 已恢复主板自动。'
        except (OSError,ValueError):
            message+=' 自动恢复未完成；请检查转速和服务日志，恢复快照已保留。'
        self.errors[channel]=message

    def tick(self):
        if not self.active: return
        # One controller operation per iteration keeps the local API responsive.
        ch=min(self.active,key=lambda ch:self.active[ch]['last'])
        r=self.active[ch];now=self.clock()
        if now-r['last']<2: return
        r['last']=now;c=self.profiles[ch]
        try:
            sensor=self.source(c['source']);temp=sensor['temperature_c']
            if sensor.get('identity',sensor['id'])!=r['sensor_identity']:
                raise ValueError('自动温度源的实际传感器已改变，停止软件曲线。')
            row=next((s for s in self.broker.status()['channels'] if s['channel']==ch),{})
            expected=(r['speed']*255+50)//100
            if row.get('mode')!='manual' or row.get('pwm')!=expected:
                self.disable(ch)
                self.active.pop(ch,None)
                self.errors[ch]='检测到其他程序或控制器改变输出，软件曲线已停止，请检查当前模式。'
                return
            r['temperature']=temp
            if temp>=r['filtered']: r['filtered']=temp
            elif temp<r['filtered']-c['hysteresis']: r['filtered']=temp+c['hysteresis']
            demand=target(c,r['filtered'])
            # An explicit 0% target is a normal stop, including fan-min alarms.
            # Restarting from zero uses the ordinary curve, without a kick pulse.
            zero=row.get('rpm')==0 and r['speed']>0 and demand>0
            if zero and r['zero_since'] is None: r['zero_since']=now
            if not zero: r['zero_since']=None
            critical=temp>=c['critical_temp']
            stopped=zero and now-r['zero_since']>=10
            alarm=row.get('alarm')==1 and row.get('rpm')!=0 and r['speed']>0 and demand>0
            emergency=critical or stopped or alarm
            speed=100 if emergency else demand
            r['state']='高温全速保护' if critical else '转速反馈报警，全速保护' if emergency else '运行中'
            r['target']=speed
            direction=(speed>r['speed'])-(speed<r['speed'])
            if direction!=r['direction']:
                r['direction']=direction;r['pending']=now
            delay=c['step_up'] if direction>0 else c['step_down']
            if direction and (emergency or now-r['pending']>=delay):
                self.broker.apply(ch,'manual',(speed*255+50)//100)
                r['speed']=speed;r['direction']=0;r['pending']=self.clock()
            if not emergency and r['speed']==0: r['state']='曲线停转（0%）'
        except (OSError,ValueError) as exc:
            self.fail(ch,str(exc) if isinstance(exc,ValueError) else '曲线读写失败。')

    def recover(self):
        # Stop releases hardware ownership, but retains the user's enabled intent.
        self.active.clear()
        self.broker.recover()
