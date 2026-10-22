"""Temperature del PC, lette da LibreHardwareMonitor.

LibreHardwareMonitor gira con i permessi di amministratore (attività "Schermo Esteso - sensori",
creata da strumenti\\sensori.ps1) e pubblica i sensori sul suo server web interno, solo con password
(salvata in dati\\certs\\sensori.json, fuori da git). Qui li leggo ogni 2 secondi in un thread a parte.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'dati')  # dati personali (fuori da git)
CONFIG = os.path.join(DATA, 'certs', 'sensori.json')
EVERY = 2.0

# sensori mostrati: (inizio dell'identificativo, nome nella finestra)
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
        self.data = None  # {'elenco': [...], 'media': x} oppure None se non disponibili
        self.error = None
        self.wanted = 0.0  # ultima volta che qualcuno ha chiesto le temperature (la finestra del PC)
        try:
            with open(CONFIG, encoding='utf-8') as f:
                c = json.load(f)
            self.url = c['url']
            self.auth = 'Basic ' + base64.b64encode(f"{c['utente']}:{c['password']}".encode()).decode()
        except (OSError, ValueError, KeyError):
            self.url = None
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            if time.monotonic() - self.wanted > 10:  # finestra del PC chiusa: non serve leggerle
                time.sleep(1)
                continue
            try:
                self.data = self._read()
                self.error = None
            except Exception as e:  # LibreHardwareMonitor chiuso o in avvio: riprovo
                self.data = None
                self.error = str(e)
            time.sleep(EVERY)

    def _read(self):
        req = urllib.request.Request(self.url, headers={'Authorization': self.auth})
        with urllib.request.urlopen(req, timeout=2) as r:
            root = json.load(r)
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
