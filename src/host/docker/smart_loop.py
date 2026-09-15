"""Run the existing fixed SMART collector; sleep disks stay asleep."""
import signal
import threading
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smart_cache

stopped = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stopped.set())
signal.signal(signal.SIGINT, lambda *_: stopped.set())
while not stopped.is_set():
    try:
        smart_cache.main()
    except (OSError, ValueError, KeyError) as exc:
        print('SMART collection unavailable: ' + type(exc).__name__, flush=True)
    stopped.wait(60)
