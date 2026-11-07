"""Finestra di controllo sul PC: console a sinistra (2/3), prestazioni a destra (1/3).

La pagina (panel/index.html) è servita solo al PC stesso (127.0.0.1), in una finestra di
Edge senza barre aperta da avvio.pyw. Riceve le righe della console e, ogni secondo, lo stato
di PC e dispositivi.
"""
import asyncio
import ctypes
import json
import os
import re
import time
from ctypes import wintypes

import finestre
import riposo
import sicurezza
from gpu import Gpu
from sensori import Sensors

from aiohttp import WSMsgType, web

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, 'panel', 'index.html')
QUALITY_NAMES = ['Massima', 'Fluida', 'Media', 'Leggera']


# --- uso di CPU e memoria del PC ----------------------------------------------
class _MEM(ctypes.Structure):
    _fields_ = [('len', wintypes.DWORD), ('load', wintypes.DWORD), ('total', ctypes.c_uint64),
                ('avail', ctypes.c_uint64), ('_r', ctypes.c_uint64 * 5)]


def _ft(f):
    return (f.dwHighDateTime << 32) | f.dwLowDateTime


class PcStats:
    def __init__(self):
        self.prev = None
        self.gpu = Gpu()
        self.sensors = Sensors()

    def read(self):
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        cur = (_ft(idle), _ft(kern) + _ft(user))  # il tempo "kernel" comprende anche quello inattivo
        cpu = None
        if self.prev:
            di, dt = cur[0] - self.prev[0], cur[1] - self.prev[1]
            cpu = round(100 * (1 - di / dt)) if dt else 0
        self.prev = cur
        m = _MEM(); m.len = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {'cpu': cpu, 'ram': m.load, 'ramGb': round((m.total - m.avail) / 2**30, 1),
                'ramTot': round(m.total / 2**30, 1), 'gpu': self.gpu.read(),
                'temp': self.sensors.read(), 'tempAttese': bool(self.sensors.url)}


class Panel:
    def __init__(self, app, log, lines, listeners, folder, urls, ui_mode, exchange):
        self.app = app
        self.exchange = exchange
        self.on_setting = lambda key, value: None  # salvataggio delle impostazioni (lo collega server.py)
        self.log = log
        self.lines = lines          # ultime righe della console
        self.listeners = listeners  # funzioni chiamate a ogni riga nuova
        self.folder = folder
        self.urls = urls
        self.ui_mode = ui_mode      # avviato dalla finestra: chiudendola si chiude tutto
        self.clients = set()
        self.pc = PcStats()
        self.prev = {}              # dispositivo -> (ora, fotogrammi, byte) per calcolare i valori al secondo
        self.started = time.monotonic()
        self.last_client = None
        self.last_clean = True
        self.paused_logged = False

    def _local(self, request):
        sicurezza.local_only(request, self.app.local_port)

    async def page(self, request):
        self._local(request)
        resp = web.FileResponse(PAGE)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    async def background(self, request):
        """Immagini di sfondo dei temi (cartella panel/sfondi), solo per la finestra di questo PC."""
        self._local(request)
        name = request.match_info['nome']
        if not re.fullmatch(r'[a-z0-9-]+\.jpg', name):
            raise web.HTTPNotFound()
        path = os.path.join(os.path.dirname(PAGE), 'sfondi', name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def ping(self, request):
        self._local(request)
        return web.json_response({'ok': True})

    # ---- stato ----
    def devices(self):
        app = self.app
        now = time.monotonic()
        out = []
        for dev_id, d in app.devices.data.items():
            s = app.sessions.get(dev_id)
            online = bool(s and s.clients)
            slot = d.get('slot')
            st = app.slots.get(slot) or {}
            if st.get('active') and app.ready.get(slot):
                screen = 'acceso'
            elif online:
                screen = 'in accensione'
            else:
                screen = 'spento'
            row = {'id': dev_id, 'nome': d.get('nome'), 'online': online, 'ip': d.get('ip'),
                   'visto': d.get('visto'), 'schermo': screen, 'numero': (slot or 0) + 1,
                   'ris': 'x'.join(map(str, d.get('risoluzione', []))) or None,
                   'livello': s.capture.level if s else d.get('qualita', 1),
                   'auto': d.get('qualita_auto', True), 'scelta': d.get('qualita_scelta', d.get('qualita', 1))}
            if s:
                t0, f0, b0 = self.prev.get(dev_id, (now, s.sent_frames, s.sent_bytes))
                dt = max(now - t0, 0.001)
                row['fpsInviati'] = round((s.sent_frames - f0) / dt) if online else 0
                row['mbit'] = round((s.sent_bytes - b0) * 8 / dt / 1e6, 1) if online else 0
                self.prev[dev_id] = (now, s.sent_frames, s.sent_bytes)
                st_ = s.stats if online and time.time() - s.stats.get('ora', 0) < 6 else {}
                row.update({'fps': st_.get('fps'), 'rtt': st_.get('rtt'), 'motore': st_.get('render'),
                            'batt': st_.get('batt'), 'carica': st_.get('carica'),
                            'qualita': QUALITY_NAMES[s.capture.level], 'salti': sum(c.dropped for c in s.clients),
                            'riavvii': s.capture.restarts})
                if s.monitor and screen == 'acceso':
                    row['ris'] = f"{s.monitor['w']}x{s.monitor['h']}"
            out.append(row)
        out.sort(key=lambda r: (not r['online'], r['numero']))
        return out

    def pins(self):
        now = time.time()
        return [{'nome': p.get('nome') or 'Dispositivo', 'pin': p['pin'], 'secondi': int(p['scade'] - now)}
                for p in self.app.devices.pins.values() if p['scade'] > now]

    def status(self):
        return {'t': 'stato', 'pc': self.pc.read(), 'dispositivi': self.devices(), 'pin': self.pins(),
                'appunti': self.app.clipboard.enabled,
                'file': self.exchange.files()}

    # ---- websocket della finestra ----
    async def ws(self, request):
        self._local(request)
        # heartbeat largo: con il PC carico (RAM piena) la finestra può rispondere in ritardo senza essere chiusa
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        self.last_client = None
        self.paused_logged = False
        queue = asyncio.Queue()

        def on_line(line):
            queue.put_nowait(line)

        self.listeners.add(on_line)
        await ws.send_str(json.dumps({'t': 'inizio', 'righe': list(self.lines), 'indirizzi': self.urls,
                                      'cartella': self.folder}))

        async def pump():
            while not ws.closed:
                batch = [await queue.get()]
                while not queue.empty():
                    batch.append(queue.get_nowait())
                await ws.send_str(json.dumps({'t': 'righe', 'righe': batch}))

        async def ticker():
            while not ws.closed:
                await ws.send_str(json.dumps(self.status()))
                await asyncio.sleep(1)

        tasks = [asyncio.create_task(pump()), asyncio.create_task(ticker())]
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                t = d.get('t')
                if t == 'cartella':
                    os.startfile(self.folder)
                elif t == 'apri':
                    self.exchange.open_file(d.get('nome', ''))
                elif t == 'qualita':
                    self.force_quality(d.get('id', ''), d.get('livello', 1))
                elif t == 'appunti':
                    on = bool(d.get('on'))
                    self.app.clipboard.enabled = on
                    self.on_setting('appunti', on)
                    self.log(f'appunti condivisi {"attivi" if on else "spenti"}', 'PC')
                elif t == 'qualita_auto':
                    dev = self.app.devices.get(d.get('id', ''))
                    if dev:
                        on = bool(d.get('on'))
                        self.app.devices.update(d['id'], qualita_auto=on)
                        self.log(f'qualità automatica {"attiva" if on else "spenta"} per {dev.get("nome")}', 'PC')
                elif t == 'dimentica':
                    await self.forget(d.get('id', ''))
                elif t == 'esci':
                    self.log('chiusura richiesta dalla finestra', 'PC')
                    self.app.stop.set()
        except Exception as e:
            self.log(f'Finestra: messaggio ignorato ({e!r})')
        finally:
            self.listeners.discard(on_line)
            self.clients.discard(ws)
            for t in tasks:
                t.cancel()
            if not self.clients:
                self.last_client = time.monotonic()
                # chiusa davvero (la finestra manda 1000/1001) o solo collegamento perso (PC carico, finestra rallentata)
                self.last_clean = ws.close_code in (1000, 1001)
                if not self.last_clean:
                    self.log('la finestra non risponde (PC carico?): aspetto che si ricolleghi, fino a 3 minuti', 'PC')
        return ws

    def force_quality(self, dev_id, level):
        """Qualità decisa dal PC: vale subito se il dispositivo è collegato, altrimenti al prossimo collegamento."""
        s = self.app.sessions.get(dev_id)
        if s:
            s.set_quality(level, 'dal PC')
        elif self.app.devices.get(dev_id):
            level = max(0, min(int(level), len(QUALITY_NAMES) - 1))
            self.app.devices.update(dev_id, qualita=level, qualita_scelta=level)

    async def forget(self, dev_id):
        d = self.app.devices.get(dev_id)
        if not d:
            return
        self.app.devices.remove(dev_id)
        s = self.app.sessions.pop(dev_id, None)
        if s:
            for c in list(s.clients):
                await c.ws.close(code=4003, message=b'dimenticato')
            await s.stop_capture()
        self.log(f'dimenticato {d["nome"]}: per ricollegarsi servirà un PIN nuovo', 'PC')
        self.app.kick.set()

    async def watch_window(self):
        """Avviato dalla finestra: quando la finestra resta chiusa per 8 s, chiudo tutto."""
        if not self.ui_mode:
            await asyncio.Future()  # avviato da terminale: resta acceso finché non lo chiudi tu
        while True:
            await riposo.nap(1)
            now = time.monotonic()
            if self.clients:
                continue
            if riposo.woke_since(30):
                # appena svegliato: Edge ci mette un po' a ricollegare la finestra, non è stata chiusa
                self.last_client = now
                continue
            never = self.last_client is None and now - self.started > 60
            attesa = 8 if self.last_clean else 180
            closed = self.last_client is not None and now - self.last_client > attesa
            if (never or closed) and await asyncio.to_thread(finestre.panel_window_open):
                # la finestra c'è ancora: Edge l'ha solo messa in pausa (coperta, PC carico). Non spengo niente,
                # e dopo un clic su di lei si ricollega da sola
                if not self.paused_logged:
                    self.log('la finestra è aperta ma in pausa (Edge): resto acceso', 'PC')
                    self.paused_logged = True
                self.last_client = now
                continue
            if never or closed:
                self.log('finestra chiusa: spengo gli schermi virtuali e chiudo', 'PC')
                self.app.stop.set()
                return

    def routes(self, router):
        router.add_get('/pc', self.page)
        router.add_get('/pc/ping', self.ping)
        router.add_get('/pc/sfondo/{nome}', self.background)
        router.add_get('/pc/ws', self.ws)
