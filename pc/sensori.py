"""PC temperatures, read from LibreHardwareMonitor.

LibreHardwareMonitor runs with administrator rights (task "Schermo Esteso - sensori",
created by tools\\sensori.ps1) and publishes the sensors on its built-in web server, password only
(saved in data\\certs\\sensori.json, outside git). Here I read them every 2 seconds in a separate thread.
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')  # personal data (outside git)
CONFIG = os.path.join(DATA, 'certs', 'sensori.json')
EVERY = 2.0

# sensors shown: (start of the identifier, name in the window)
SHOWN = [
    (re.compile(r'^/(intelcpu|amdcpu)/\d+/temperature/'), 'CPU'),
    (re.compile(r'^/(nvme|hdd|ssd)/\d+/temperature/0$'), 'Disco'),
    (re.compile(r'^/(gpu-nvidia|gpu-amd|gpu-intel)/\d+/temperature/0$'), 'Scheda grafica'),
    (re.compile(r'^/battery/\d+/temperature/'), 'Batteria'),
]


def _number(text):
    m = re.search(r'-?\d+(?:[.,]\d+)?', text or '')
    return float(m.group().replace(',', '.')) if m else None


class Sensors:
    def __init__(self):
        self.data = None  # {'elenco': [...], 'media': x} or None if not available
        self.error = None
        self.wanted = 0.0  # last time someone asked for the temperatures (the PC window)
        try:
            with open(CONFIG, encoding='utf-8') as f:
                c = json.load(f)
            self.url = c['url']
            basic = lambda pw: 'Basic ' + base64.b64encode(f"{c['utente']}:{pw}".encode()).decode()
            # LibreHardwareMonitor 0.9.6 stores only the SHA-256 fingerprint of the password and, after a restart, accepts
            # that instead of the password: I try the password and, if it's refused, the fingerprint
            self.auths = [basic(c['password']), basic(hashlib.sha256(c['password'].encode()).hexdigest())]
        except (OSError, ValueError, KeyError):
            self.url = None
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            if time.monotonic() - self.wanted > 10:  # PC window closed: no need to read them
                time.sleep(1)
                continue
            try:
                self.data = self._read()
                self.error = None
            except Exception as e:  # LibreHardwareMonitor closed or starting: retry
                self.data = None
                self.error = str(e)
            time.sleep(EVERY)

    def _read(self):
        for i, auth in enumerate(self.auths):
            req = urllib.request.Request(self.url, headers={'Authorization': auth})
            try:
                with urllib.request.urlopen(req, timeout=2) as r:
                    root = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code != 401 or i == len(self.auths) - 1:
                    raise
        if i:  # next time start from the one that worked
            self.auths.reverse()
        found = []

        def walk(n):
            sid = n.get('SensorId', '')
            if '/temperature/' in sid and 'Distance' not in n.get('Text', ''):
                for rx, label in SHOWN:
                    if rx.match(sid):
                        v = _number(n.get('Value'))
                        if v is not None and 0 < v < 130:
                            found.append((label, n.get('Text', ''), v))
                        break
            for ch in n.get('Children', []):
                walk(ch)

        walk(root)
        out = []
        cpu = {t: v for label, t, v in found if label == 'CPU'}
        if cpu:
            main = cpu.get('CPU Package') or cpu.get('Core (Tctl/Tdie)') or cpu.get('Core Max') or max(cpu.values())
            cores = [round(v) for t, v in cpu.items() if re.match(r'CPU Core #\d+$', t)]
            out.append({'nome': 'CPU', 'valore': round(main), 'core': cores})
        for label in ('Scheda grafica', 'Disco', 'Batteria'):
            vals = [v for lb, t, v in found if lb == label]
            if vals:
                out.append({'nome': label, 'valore': round(max(vals))})
        if not out:
            return None
        return {'elenco': out, 'media': round(sum(x['valore'] for x in out) / len(out))}

    def read(self):
        self.wanted = time.monotonic()
        return self.data
