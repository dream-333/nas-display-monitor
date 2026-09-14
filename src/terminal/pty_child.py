#!/usr/bin/env python3
"""Obtain a controlling TTY, then exec SSH. Invoked only by the local service."""
import fcntl
import os
import sys
import termios

if __name__ == '__main__':
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execv('/usr/bin/ssh', ['/usr/bin/ssh', *sys.argv[1:]])
