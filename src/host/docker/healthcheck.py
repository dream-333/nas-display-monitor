"""Probe the web process without logging in or reading credentials."""
import json
import os
import urllib.request

port = int(os.environ.get('NAS_DISPLAY_PORT', '8787'))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(f'http://127.0.0.1:{port}/api/session', timeout=3) as response:
    value = json.load(response)
    assert response.status == 200 and value.get('authenticated') is False and value.get('csrf')
