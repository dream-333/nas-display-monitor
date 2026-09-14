#!/usr/bin/env python3
"""Withdrawn: automatic-mode readback did not prove safe fan recovery."""

REASON = (
    '此测试已停用：Zero1 pro 实测切回 pwm3_enable=2 后 fan3 连续读到 0 RPM。'
    '没有执行任何硬件写入。请使用 FAN-CHECK.py 只读检查，勿再次运行旧副本。'
)


def pulse(*args, **kwargs):
    raise RuntimeError(REASON)


def main():
    raise SystemExit(REASON)


if __name__ == '__main__':
    main()
