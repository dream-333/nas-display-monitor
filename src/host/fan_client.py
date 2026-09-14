"""Non-privileged client of the fixed local fan-control broker."""
import json
import os
import socket
import re


def valid_channel(value):
    return isinstance(value, str) and (value in ("pwm2", "pwm3") or bool(re.fullmatch(r"hwmon-[0-9a-f]{16}-pwm[1-9][0-9]?", value)))


def display_text(value):
    """Keep driver diagnostics meaningful without exposing register notation."""
    if not value: return value
    text = str(value)
    for old in ('自动模式下该寄存器是起始 PWM，不是实时占空比。',
                ' 自动时寄存器为起始 PWM，不是实时占空比。'):
        text = text.replace(old, '自动模式由主板调节，不显示固定百分比。')
    text = re.sub(r'恢复接管前的自动策略（模式 [2-5]）。', '恢复接管前的自动策略。', text)
    text = re.sub(r'pwm([0-9]+)', r'通道 \1', text, flags=re.I)
    return text.replace('PWM/DC 类型', '电气控制方式').replace('PWM', '速度设置').replace('0–255', '0–100%')


def display_status(status):
    if status is None: return None
    result = {**status, 'error': display_text(status.get('error')), 'note': display_text(status.get('note'))}
    rows = []
    for original in status.get('channels', []):
        row = {k: v for k, v in original.items() if k not in ('pwm', 'register')}
        raw = original.get('pwm')
        row['speed_percent'] = ((raw * 100 + 127) // 255
                                if original.get('mode') in ('manual', 'curve') and type(raw) is int and 0 <= raw <= 255 else None)
        row['label'] = display_text(original.get('label') or original.get('fan') or original['channel'])
        row['note'] = display_text(original.get('note'))
        rows.append(row)
    result['channels'] = rows
    return result


def request(data):
    path = os.environ.get('NAS_DISPLAY_FAN_SOCKET')
    if not path: raise ValueError('此安装方式未启用风扇控制服务。')
    service = 'nas-display-fnos-fan' if 'fnos' in path else 'nas-display-fan'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(6)
            sock.connect(path)
            sock.sendall(json.dumps(data).encode() + b'\n')
            raw = b''
            while not raw.endswith(b'\n') and len(raw) <= 65536:
                chunk = sock.recv(65537 - len(raw))
                if not chunk: break
                raw += chunk
            if len(raw) > 65536: raise ValueError('风扇服务响应过大。')
            result = json.loads(raw)
    except FileNotFoundError:
        raise ValueError(f'风扇控制服务尚未创建通信接口，请检查 {service} 服务是否启动。') from None
    except PermissionError:
        raise ValueError('网页没有访问风扇控制接口的权限，请检查服务用户与 socket 权限。') from None
    except ConnectionRefusedError:
        raise ValueError(f'风扇控制接口没有服务监听，请检查 {service} 服务日志。') from None
    except TimeoutError:
        raise ValueError('风扇控制服务响应超时；操作可能仍在进行，请先查看实际模式与转速。') from None
    except OSError:
        raise ValueError(f'风扇控制服务连接中断，请检查 {service} 服务日志。') from None
    except ValueError:
        raise ValueError('风扇控制服务响应格式无效，请检查前后台版本是否一致。') from None
    if not isinstance(result, dict) or not result.get('ok'):
        raise ValueError(str(result.get('error', '风扇操作未完成。')) if isinstance(result, dict) else '风扇服务响应无效。')
    if not isinstance(result.get('status'), dict):
        raise ValueError('风扇服务响应无效。')
    return result['status']


def status():
    if not os.environ.get('NAS_DISPLAY_FAN_SOCKET'): return None
    try: return request({'action': 'status'})
    except ValueError as exc: return {'available': False, 'error': str(exc), 'channels': []}
