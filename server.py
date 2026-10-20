"""Schermo Esteso: usa iPad, iPhone e altri dispositivi come schermi aggiuntivi di Windows.

Avvio: python server.py  (oppure "Avvia.bat"), poi sul dispositivo apri in Safari
l'indirizzo mostrato. Un dispositivo nuovo deve inserire il PIN che compare qui.

Ogni dispositivo ha il suo schermo virtuale: si accende quando il dispositivo si collega
e si spegne 15 s dopo che si è scollegato. Prima di spegnerlo, le sue finestre vengono
portate sul monitor principale, sotto quelle già aperte.
"""
import asyncio
import collections
import ctypes
import json
import os
import socket
import sys
import threading
import time

from aiohttp import WSMsgType, web

import appunti
import certificati
import qualita
import sicurezza
import trascina
import finestre
import monitors
import vdd
from capture import Capture
from devices import MAX_DEVICES, Devices
import inject
from inject import Injector, Pad
from pannello import Panel
from scambio import FOLDER, Exchange

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'config.json')
DEVICES = os.path.join(HERE, 'dispositivi.json')
DEFAULTS = {
    'port': 8770,         # porta per i dispositivi (HTTPS)
    'porta_locale': 8769,  # porta solo per questo PC (finestra di controllo, Invia a), senza cifratura
    'quality': 1,       # livello di partenza per i dispositivi nuovi (0 Massima .. 3 Leggera)
    'bufsize': '1M',    # buffer piccolo = meno ritardo
    'encoder': 'auto',  # auto | qsv | x264
    'appunti': True,    # appunti condivisi tra PC e dispositivi
}
QUALITY_LABELS = ['Massima', 'Fluida', 'Media', 'Leggera']
MAX_QUEUE = 8        # fotogrammi in coda per client oltre i quali salto al prossimo fotogramma completo
GRACE = 15           # secondi dopo la chiusura dell'app prima di spegnere il suo schermo
STARTUP_GRACE = 20   # all'avvio aspetto un po' i dispositivi prima di spegnere gli schermi senza nessuno
MIN_HEIGHT_PT = 768    # altezza minima dello schermo per Windows, in punti (pixel / ridimensionamento)
DISPLAY_GAP = 3      # secondi minimi tra due cambi di configurazione degli schermi
RELOAD_WAIT = 8      # secondi di attesa dopo il riavvio del driver


LOG_LINES = collections.deque(maxlen=3000)  # ultime righe, per la finestra di controllo
LOG_LISTENERS = set()
LOG_FILE = os.path.join(HERE, 'server.log')


def log(msg, chi=None):
    """Riga di console. chi = dispositivo (o 'PC') che ha fatto l'azione: compare come [iPad]."""
    if chi:
        msg = f'[{chi}] {msg}'
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)  # senza terminale (pythonw) non stampa nulla
    LOG_LINES.append(line)
    for fn in list(LOG_LISTENERS):
        fn(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding='utf-8') as f:
            cfg.update(json.load(f))
    for k in ('fps', 'bitrate', 'maxrate', 'gop', 'pin'):  # vecchie chiavi non più usate
        cfg.pop(k, None)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return cfg


def save_setting(key, value):
    """Cambia una sola impostazione in config.json (dalla finestra del PC)."""
    cfg = load_config()
    cfg[key] = value
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith('127.'):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


# --- PC sempre acceso mentre c'è un dispositivo collegato ----------------------
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2


def keep_pc_awake(on):
    # se gli schermi del PC si spengono, Windows spegne anche quelli virtuali
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.queue = collections.deque()
        self.wake = asyncio.Event()
        self.need_key = True
        self.dropped = 0

    def push(self, msg, key):
        if self.need_key and not key:
            return
        if len(self.queue) >= MAX_QUEUE:
            # la rete è rimasta indietro: butto la coda e riparto dal prossimo fotogramma completo,
            # così il ritardo non si accumula
            self.queue.clear()
            self.dropped += 1
            if not key:
                self.need_key = True
                return
        self.need_key = False
        self.queue.append(msg)
        self.wake.set()

    def push_text(self, text):
        self.queue.append(text)
        self.wake.set()

    async def sender(self):
        while not self.ws.closed:
            await self.wake.wait()
            self.wake.clear()
            while self.queue and not self.ws.closed:
                m = self.queue.popleft()
                if isinstance(m, str):
                    await self.ws.send_str(m)
                else:
                    await self.ws.send_bytes(m)


class Session:
    """Un dispositivo abbinato: il suo schermo virtuale, la sua cattura video, i suoi tocchi."""

    def __init__(self, app, dev_id):
        self.app = app
        self.id = dev_id
        self.clients = set()
        self.last_seen = time.monotonic()
        self.config_msg = None
        self.gop = []  # fotogrammi dall'ultimo fotogramma completo, per chi si collega a metà
        self.pad = Pad(app.injector)
        self.monitor = None
        self.capture_key = None
        settings = dict(app.cfg)
        settings['quality'] = self.info.get('qualita', app.cfg['quality'])
        self.capture = Capture(settings, self.target, self.on_config, self.on_frame,
                               lambda m: log(m, self.name))
        self.capture_task = None
        self.stats = {}
        self.sent_frames = 0  # per le prestazioni nella finestra del PC
        self.sent_bytes = 0
        self.auto = qualita.AutoQuality(time.monotonic())
        self.judged = (time.monotonic(), 0, 0)  # (ora, fotogrammi inviati, saltati) all'ultimo giudizio

    @property
    def info(self):
        return self.app.devices.get(self.id) or {}

    @property
    def name(self):
        return self.info.get('nome', 'dispositivo')

    @property
    def slot(self):
        return self.info.get('slot')

    def wants_screen(self):
        return bool(self.clients) or time.monotonic() - self.last_seen < GRACE

    def device_name(self):
        """\\\\.\\DISPLAYn dello schermo di questo dispositivo, se acceso."""
        s = self.app.slots.get(self.slot)
        return s['device'] if s and s['active'] else None

    async def target(self):
        if not self.app.ready.get(self.slot):
            return None
        dev = self.device_name()
        m = dev and monitors.by_device(dev)
        if not m:
            return None
        idx = monitors.dxgi_outputs().get(dev)
        if idx is None:
            return None
        self.monitor = m
        self.pad.set_monitor(m)
        self.capture_key = (dev, idx, m['x'], m['y'], m['w'], m['h'])
        return idx, m['w'], m['h']

    def start_capture(self):
        if not self.capture_task or self.capture_task.done():
            self.capture_task = asyncio.create_task(self.capture.run())

    async def stop_capture(self):
        if self.capture_task and not self.capture_task.done():
            self.capture.stop()
            try:
                await asyncio.wait_for(self.capture_task, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.capture_task.cancel()
        self.capture_task = None
        self.capture_key = None
        self.pad.set_monitor(None)
        self.pad.release_all()

    def on_config(self, cfg):
        self.config_msg = json.dumps(cfg)
        self.gop = []
        for c in self.clients:
            c.queue.clear()
            c.need_key = True
            c.push_text(self.config_msg)

    def on_frame(self, data, key):
        msg = (b'\x01' if key else b'\x00') + data
        self.sent_frames += 1
        self.sent_bytes += len(msg)
        if key:
            self.gop = [msg]
        elif self.gop and len(self.gop) < 400:
            self.gop.append(msg)
        for c in self.clients:
            c.push(msg, key)

    def send_gop(self, c):
        c.queue.clear()
        c.need_key = True
        if self.config_msg:
            c.push_text(self.config_msg)
        if self.gop:
            # tutto il gruppo in una volta (senza il limite di coda): il dispositivo lo decodifica di corsa
            c.queue.extend(self.gop)
            c.need_key = False
            c.wake.set()

    def set_area(self, w, h):
        """Risoluzione su misura: lo schermo virtuale prende esattamente l'area utile del dispositivo
        (es. tutto lo schermo dell'iPad tranne la barra con ora e batteria), così niente bande nere."""
        try:
            w, h = int(w), int(h)
        except (TypeError, ValueError):
            return
        if not (640 <= w <= 4096 and 480 <= h <= 4096 and 1.0 <= w / h <= 2.5):
            return  # solo orizzontale e misure sensate
        w, h = w // 2 * 2, h // 2 * 2  # pari, come chiede il codificatore H.264
        if list(self.info.get('risoluzione', [])) == [w, h]:
            return
        log(f'nuova area utile {w}x{h}: adatto lo schermo virtuale', self.name)
        self.app.devices.update(self.id, risoluzione=[w, h], configurato=False)
        self.app.kick.set()

    def set_quality(self, level, source, auto=False):
        """Qualità (fotogrammi al secondo e risoluzione). La ricorda il PC: la può cambiare il dispositivo
        dal suo menu oppure il PC, che la impone anche se il dispositivo è troppo lento per rispondere.
        Scelta a mano = il massimo a cui la qualità automatica può tornare."""
        try:
            level = int(level)
        except (TypeError, ValueError):
            return
        old = self.capture.level
        level = self.capture.apply_level(level)
        if auto:
            self.app.devices.update(self.id, qualita=level)
        else:
            self.app.devices.update(self.id, qualita=level, qualita_scelta=level)
        self.auto.changed(time.monotonic())
        msg = json.dumps({'t': 'quality', 'level': level, 'auto': auto})
        for c in self.clients:
            c.queue.appendleft(msg)  # prima del video in coda: arriva subito anche se la rete è intasata
            c.wake.set()
        if level != old:
            log(f'qualità {QUALITY_LABELS[level]} ({source}): riavvio la cattura', self.name)
            self.capture.restart()

    async def select_word(self, c, x, y, delivered=False):
        """Doppio tocco sul dispositivo: seleziona la parola. Nei browser con un doppio clic del mouse (col tocco la parola non si
        seleziona anche nei browser (che con il tocco non lo fanno). Aspetto un attimo: subito dopo
        un tocco Edge ignora i clic del mouse."""
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return
        pt = self.pad.point(x, y)
        if pt and finestre.browser_at(*pt):
            # Edge conta i clic del mouse insieme ai tocchi arrivati da meno di ~0,5 s: aspetto solo il necessario
            await asyncio.sleep(max(0.0, 0.55 - (time.monotonic() - self.pad.last_touch)))
            for _ in range(2):
                self.pad.mouse('click', 0, x, y)
                await asyncio.sleep(0.03)
        elif not delivered:
            # programmi classici (Blocco note, Word…): il doppio tocco di Windows seleziona già la parola
            self.pad.touch('d', 'parola', x, y)
            await asyncio.sleep(0.03)
            self.pad.touch('u', 'parola', x, y)
            await asyncio.sleep(0.15)
        else:
            await asyncio.sleep(0.15)
        c.queue.appendleft(json.dumps({'t': 'parola_ok'}))  # ora il dispositivo mostra Copia/Incolla
        c.wake.set()

    def check_quality(self, stats=None):
        """Qualità automatica: chiamata a ogni statistica del dispositivo (circa ogni 2 s) e, se il
        dispositivo tace, dal controllo periodico."""
        if not self.info.get('qualita_auto', True) or not self.clients:
            return
        now = time.monotonic()
        t0, f0, d0 = self.judged
        drops = sum(c.dropped for c in self.clients)
        sent = (self.sent_frames - f0) / max(now - t0, 0.5)
        self.judged = (now, self.sent_frames, drops)
        best = self.info.get('qualita_scelta', self.info.get('qualita', 1))
        if stats is None:
            new, why = self.auto.judge(now, self.capture.level, best, sent=sent, silent=True)
        else:
            new, why = self.auto.judge(now, self.capture.level, best, rtt=stats.get('rtt'),
                                       shown=stats.get('dec'), sent=sent, drops=max(drops - d0, 0))
        if new is not None:
            verb = 'abbassata' if new > self.capture.level else 'rialzata'
            self.set_quality(new, f'{verb} in automatico: {why}', auto=True)

    def handle(self, c, d):
        t = d.get('t')
        pad = self.pad
        if t == 'p':
            pad.touch(d['e'], d['id'], d['x'], d['y'])
        elif t == 'm':
            pad.mouse(d['e'], d.get('b', 0), d['x'], d['y'])
        elif t == 'w':
            pad.wheel(d['x'], d['y'], d.get('dx', 0), d.get('dy', 0))
        elif t == 'area':
            self.set_area(d.get('w'), d.get('h'))
        elif t == 'apri':
            asyncio.create_task(self.app.open_here(self, str(d.get('nome', ''))))
        elif t == 'pen':
            use = pad.pen(d['e'], d['x'], d['y'], float(d.get('p', 0.5)), d.get('tx', 0), d.get('ty', 0),
                          bool(d.get('er')), bool(d.get('auto')))
            if use and (use, pad.pen_app) != getattr(self, 'pen_seen', None):
                self.pen_seen = (use, pad.pen_app)
                where = f' in {pad.pen_app}' if pad.pen_app else ''
                log(f'Pencil{where}: ' + ('scrive' if use == 'penna' else 'fa da dito (scorre e tocca)'), self.name)
            if use:  # al dispositivo: se in questo tratto la Pencil scrive o fa da dito (niente inchiostro)
                c.queue.appendleft(json.dumps({'t': 'penna', 'uso': use}))
                c.wake.set()
        elif t == 'ping':
            c.push_text(json.dumps({'t': 'pong', 'ts': d.get('ts'), 'drop': c.dropped,
                                    'restarts': self.capture.restarts}))
        elif t == 'key':
            self.send_gop(c)
        elif t == 'quality':
            self.set_quality(d.get('level', 0), 'dal dispositivo')
        elif t == 'appunti':
            self.app.clipboard.from_device_text(self, d.get('testo'))
        elif t == 'scrivi':
            text = d.get('testo')
            if isinstance(text, str) and text:
                inject.type_text(text)
                # mai il contenuto nella console: per le password neanche la lunghezza
                log('password digitata sul PC' if d.get('segreto') else f'testo digitato sul PC ({len(text)} caratteri)',
                    self.name)
        elif t == 'tasto':
            inject.press(str(d.get('k', '')))
        elif t == 'deposita':
            asyncio.create_task(self.app.drop_file(self, d.get('nome', ''), d.get('x'), d.get('y')))
        elif t == 'parola':
            asyncio.create_task(self.select_word(c, d.get('x'), d.get('y'), bool(d.get('gia'))))
        elif t == 'diag':
            d.pop('t', None)
            log(f'info: {json.dumps(d, ensure_ascii=False)}', self.name)
        elif t == 'stats':
            if d.get('pausa'):  # il dispositivo è stato in pausa (cambio app, Centro di controllo): dati non validi
                self.stats['ora'] = time.time()
                self.auto.changed(time.monotonic())
                return
            self.stats = {k: d.get(k) for k in ('fps', 'dec', 'rtt', 'mbit', 'queue', 'render', 'q', 'batt')}
            self.stats['ora'] = time.time()  # mostrate nella finestra del PC, non nella console
            self.check_quality(d)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.devices = Devices(DEVICES)
        self.injector = Injector(log)
        self.sessions = {}
        self.slots = {}     # stato degli schermi virtuali (da monitors.virtual_slots)
        self.ready = {}     # slot -> True quando lo schermo è acceso e configurato
        self.started = time.monotonic()
        self.last_change = 0.0
        self.awake = False
        self.kick = asyncio.Event()
        self.stop = asyncio.Event()  # chiusura ordinata (dalla finestra del PC)
        self.pair_fails = []   # orari dei PIN sbagliati (tutti i dispositivi)
        self.pair_block = 0.0  # abbinamenti bloccati fino a quest'ora
        self.reposition = {}  # slot -> (x, y) da ripristinare dopo un riavvio del driver
        self.mode_tries = {}  # slot -> tentativi di rimettere la risoluzione su misura

    def session(self, dev_id):
        s = self.sessions.get(dev_id)
        if not s:
            s = self.sessions[dev_id] = Session(self, dev_id)
        return s

    # ---- connessione e abbinamento ----
    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=1 << 20, compress=False)
        await ws.prepare(request)
        q = request.query
        dev_id = q.get('dev', '')[:64]
        if not dev_id:
            await ws.close(code=4002, message=b'aggiorna')
            return ws
        if not self.devices.check(dev_id, q.get('token', '')):
            if not await self.pair(ws, dev_id, request.remote):
                if not ws.closed:
                    await ws.close()
                return ws
        sock = request.transport.get_extra_info('socket')
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        s = self.session(dev_id)
        c = Client(ws)
        s.clients.add(c)
        s.stats = {'ora': time.time()}  # da qui conto il silenzio del dispositivo
        s.auto.changed(time.monotonic())  # appena ricollegato (es. tornato dall'app switcher): non giudico subito
        s.last_seen = time.monotonic()
        self.devices.update(dev_id, visto=time.strftime('%Y-%m-%d %H:%M'), ip=request.remote)
        log(f'collegato da {request.remote}', s.name)
        self.kick.set()
        s.start_capture()
        s.send_gop(c)
        c.queue.appendleft(json.dumps({'t': 'quality', 'level': s.capture.level}))  # la qualità la ricorda il PC
        # per primo, prima del video; con gli indirizzi del PC: se cambia rete (es. hotspot) l'app li prova
        port = self.cfg.get('port', 8770)
        c.queue.appendleft(json.dumps({'t': 'hello', 'nome': s.name,
                                       'indirizzi': [f'{ip}:{port}' for ip in lan_ips()]}))
        c.wake.set()
        sender = asyncio.create_task(c.sender())
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    s.handle(c, json.loads(msg.data))
                except Exception as e:  # un messaggio sbagliato non deve far cadere la connessione
                    log(f'messaggio ignorato: {e!r}')
        finally:
            s.clients.discard(c)
            s.last_seen = time.monotonic()
            if not s.clients:
                s.pad.release_all()
                log(f'scollegato: se non torna entro {GRACE} s spengo il suo schermo', s.name)
            sender.cancel()
            self.kick.set()
        return ws

    async def pair(self, ws, dev_id, remote):
        """Abbinamento con PIN. Restituisce True se il dispositivo è stato accettato."""
        await ws.send_str(json.dumps({'t': 'pair'}))
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            if d.get('t') == 'hello':  # il dispositivo si presenta: mostro il PIN sul PC
                who = sicurezza.clean_text(d.get('nome')) or 'Dispositivo'
                if time.monotonic() < self.pair_block:
                    await ws.send_str(json.dumps({'t': 'pinerr', 'bloccato': int(self.pair_block - time.monotonic())}))
                    return False
                got = self.devices.pin_for(dev_id, who)
                if not got:  # troppe richieste di abbinamento aperte insieme
                    log(f'Richiesta di abbinamento da {remote} rifiutata: ce ne sono già troppe in corso', who)
                    await ws.send_str(json.dumps({'t': 'pinerr', 'occupato': True}))
                    return False
                pin, new = got
                if new:
                    log('')
                    log(f'*** dispositivo nuovo da {remote}: chiede di collegarsi.', who)
                    log(f'*** PIN da inserire sul dispositivo: {pin}   (valido 5 minuti)', who)
                    log('')
                continue
            if d.get('t') != 'pin':
                continue
            if time.monotonic() < self.pair_block:
                await ws.send_str(json.dumps({'t': 'pinerr', 'bloccato': int(self.pair_block - time.monotonic())}))
                return False
            res = self.devices.try_pin(dev_id, str(d.get('pin', ''))[:12])
            if res != 'ok':
                # limite per tutto il PC: chi prova a indovinare aprendo tante richieste insieme
                # viene fermato lo stesso
                now = time.monotonic()
                self.pair_fails = [t for t in self.pair_fails if now - t < 600] + [now]
                if len(self.pair_fails) >= 20:
                    self.pair_block = now + 600
                    self.pair_fails = []
                    self.devices.pins.clear()
                    log(f'*** Troppi PIN sbagliati (ultimo da {remote}): abbinamenti bloccati per 10 minuti')
            if res == 'ok':
                try:
                    w, h = int(d.get('w', 0)), int(d.get('h', 0))
                    dpr = float(d.get('dpr', 1))
                except (TypeError, ValueError):
                    w = h = 0
                    dpr = 1.0
                w, h = max(w, h), min(w, h)  # orizzontale
                if not (640 <= w <= 4096 and 480 <= h <= 4096):
                    w, h = 2048, 1536
                w, h = w // 2 * 2, h // 2 * 2  # misure pari, richieste dal codificatore H.264
                name = sicurezza.clean_text(d.get('nome')) or 'Dispositivo'
                token, replaced = self.devices.pair(dev_id, name, (w, h), dpr, remote)
                for old_id in replaced:  # lo stesso iPad già abbinato (es. da Safari): chiudo la voce vecchia
                    old_s = self.sessions.pop(old_id, None)
                    if old_s:
                        for c in list(old_s.clients):
                            await c.ws.close(code=4003, message=b'sostituito')
                        await old_s.stop_capture()
                if replaced:
                    log(f'stesso dispositivo già abbinato da {remote}: sostituite {len(replaced)} voci precedenti', name)
                if not token:
                    log(f'Abbinamento rifiutato: ci sono già {MAX_DEVICES} dispositivi abbinati')
                    await ws.send_str(json.dumps({'t': 'full', 'max': MAX_DEVICES}))
                    return False
                log(f'abbinato (schermo {self.devices.get(dev_id)["slot"] + 1}, {w}x{h})', name)
                await ws.send_str(json.dumps({'t': 'paired', 'token': token}))
                return True
            if res == 'nuovo' and time.monotonic() >= self.pair_block:
                got = self.devices.pin_for(dev_id)
                if got:
                    log(f'*** Troppi tentativi o PIN scaduto: nuovo PIN {got[0]}')
            await ws.send_str(json.dumps({'t': 'pinerr', 'nuovo': res == 'nuovo'}))
            await asyncio.sleep(1)  # rallenta chi prova a indovinare
        return False

    async def open_here(self, s, name):
        """Apre sul PC un file condiviso e ne porta la finestra sullo schermo del dispositivo."""
        path = self.exchange.path_of(name)
        if not path:
            log(f'vuole aprire {name}, ma il file non è più nella cartella', s.name)
            return
        if not sicurezza.safe_to_open(path):
            # programmi e script arrivati da un dispositivo non si eseguono mai sul PC
            log(f'vuole aprire {name} sul PC: rifiutato, questo tipo di file potrebbe essere un programma', s.name)
            return
        m = s.monitor
        if not m or not s.capture_key:
            log(f'vuole aprire {name}, ma il suo schermo non è acceso', s.name)
            return
        log(f'aperto file {name} sul PC, sul suo schermo', s.name)
        await asyncio.to_thread(finestre.open_on, path, m, log, 12, os.path.join(HERE, '.edge-profilo'))

    async def drop_file(self, s, name, nx, ny):
        """File trascinato dal dispositivo sul suo schermo: sul PC faccio un trascinamento vero nello stesso
        punto, così decide il programma che sta lì (Esplora file e desktop lo copiano, Obsidian lo mette
        nella cartella del vault, Word lo inserisce…). Se nessuno lo prende, lo apro col suo programma."""
        path = self.exchange.path_of(str(name))
        m = s.monitor
        pt = s.pad.point(nx, ny) if isinstance(nx, (int, float)) and isinstance(ny, (int, float)) else None
        if not path or not m or not s.capture_key or not pt:
            return
        under = finestre.process_name(finestre.user32.WindowFromPoint(finestre.wintypes.POINT(*pt))) or 'il desktop'
        try:
            effect = await asyncio.to_thread(trascina.drop_at, path, *pt)
        except Exception as e:
            log(f'Trascinamento di {name} non riuscito: {e!r}')
            effect = None
        if effect:
            log(f'ha trascinato {name} su {under}: {effect}', s.name)
            return
        if not sicurezza.safe_to_open(path):
            log(f'ha trascinato {name} su {under}, che non lo ha preso. È nella cartella condivisa (questo tipo '
                'di file non lo apro da solo: potrebbe essere un programma)', s.name)
            return
        log(f'ha trascinato {name} su {under}, che non lo ha preso: lo apro con il suo programma', s.name)
        await asyncio.to_thread(finestre.open_on, path, m, log, 12, os.path.join(HERE, '.edge-profilo'))

    # ---- schermi ----
    async def display_change(self, what, fn, *args):
        """Un cambio di configurazione alla volta, distanziati: cambi ravvicinati possono bloccare Windows."""
        wait = self.last_change + DISPLAY_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        log(what)
        try:
            return await asyncio.to_thread(fn, *args)
        finally:
            self.last_change = time.monotonic()

    def refresh_slots(self):
        self.slots = monitors.virtual_slots()

    async def ensure_driver(self, wanted):
        """Il driver deve avere abbastanza schermi e tutte le risoluzioni dei dispositivi.
        Restituisce True se l'ha dovuto riavviare."""
        count, res = vdd.read_settings()
        need = max([count] + [s + 1 for s in wanted])
        sizes = [tuple(d['risoluzione']) for d in self.devices.data.values() if d.get('risoluzione')]
        missing = [r for r in sizes if r not in res]
        if need == count and not missing:
            return False
        # il riavvio rimescola le posizioni: mi segno dove sono ora gli schermi accesi
        owners = {d['slot']: dev for dev, d in self.devices.data.items()}
        for sl, st in self.slots.items():
            m = st['active'] and monitors.by_device(st['device'])
            if m:
                self.reposition[sl] = (m['x'], m['y'])
                if sl in owners:
                    self.devices.update(owners[sl], posizione=[m['x'], m['y'], m['w'], m['h']])
        if not vdd.write_settings(count=need, add=missing):
            return False
        await self.display_change(f'Preparo il driver: {need} schermi virtuali (gli schermi virtuali '
                                  'si spengono e riaccendono per un attimo)', vdd.reload)
        await asyncio.sleep(RELOAD_WAIT)
        self.last_change = time.monotonic()
        return True

    async def reconcile_once(self):
        """Un passo verso lo stato voluto: accende, configura o spegne al massimo uno schermo."""
        self.refresh_slots()
        wanted = {s.slot: s for s in self.sessions.values() if s.slot is not None and s.wants_screen()}

        # serve un riavvio del driver (dispositivo nuovo o risoluzione nuova)?
        if any(sl not in self.slots for sl in wanted):
            if await self.ensure_driver(wanted):
                for sl in list(self.ready):
                    self.ready[sl] = False
                return True

        # accendi
        for sl, s in wanted.items():
            st = self.slots.get(sl)
            if st and not st['active']:
                geo = s.info.get('posizione')
                r = await self.display_change(f'Accendo lo schermo di {s.name}', monitors.attach_slot, sl, geo and tuple(geo))
                if r != 0:
                    log(f'Accensione non riuscita (codice {r})')
                return True

        # configura (solo la prima volta per ogni dispositivo: risoluzione, poi ridimensionamento)
        for sl, s in wanted.items():
            st = self.slots.get(sl)
            if not (st and st['active']):
                continue
            if not s.info.get('configurato'):
                w, h = s.info['risoluzione']
                m = monitors.by_device(st['device'])
                if m and (m['w'], m['h']) != (w, h):
                    if (w, h) in monitors.list_modes(st['device']):
                        await self.display_change(f'Imposto la risoluzione {w}x{h} per {s.name}',
                                                  monitors.set_mode, st['device'], w, h)
                        return True
                    if await self.ensure_driver(wanted):  # risoluzione nuova: il driver la deve conoscere
                        for k in list(self.ready):
                            self.ready[k] = False
                        return True
                    log(f'Risoluzione {w}x{h} non disponibile per {s.name}: tengo {m["w"]}x{m["h"]}')
                # il ridimensionamento lo scelgo solo per i dispositivi appena abbinati: per gli altri
                # tengo quello impostato in Windows
                if s.info.get('scala_da_impostare'):
                    scale = min(200, max(100, round(s.info.get('dpr', 1) * 100 / 25) * 25))
                    # schermi bassi (iPhone in orizzontale): almeno ~768 punti di altezza, se no i menu
                    # delle app escono dallo schermo
                    while scale > 100 and h * 100 / scale < MIN_HEIGHT_PT:
                        scale -= 25
                    if monitors.get_scale(st['device']) != scale:
                        await self.display_change(f'Imposto il ridimensionamento {scale}% per {s.name}',
                                                  monitors.set_scale, st['device'], scale)
                self.devices.update(s.id, configurato=True, scala_da_impostare=False)
                return True
            if sl in self.reposition:
                x, y = self.reposition.pop(sl)
                m = monitors.by_device(st['device'])
                if m and (m['x'], m['y']) != (x, y):
                    await self.display_change(f'Rimetto lo schermo di {s.name} al suo posto',
                                              monitors.set_slot_position, sl, x, y)
                    return True
            m = monitors.by_device(st['device'])
            if m:
                # la risoluzione su misura può andare persa (riavvio del driver, cambi di Windows): la rimetto
                want = tuple(s.info.get('risoluzione') or (m['w'], m['h']))
                if (m['w'], m['h']) != want and self.mode_tries.get(sl, 0) < 2                         and want in monitors.list_modes(st['device']):
                    self.mode_tries[sl] = self.mode_tries.get(sl, 0) + 1
                    await self.display_change(f'Rimetto la risoluzione {want[0]}x{want[1]} per {s.name}',
                                              monitors.set_mode, st['device'], *want)
                    return True
                if (m['w'], m['h']) == want:
                    self.mode_tries.pop(sl, None)
                # posizione sempre aggiornata: alla prossima accensione torna dove lo usi davvero
                geo = [m['x'], m['y'], m['w'], m['h']]
                if s.info.get('posizione') != geo:
                    self.devices.update(s.id, posizione=geo)
            if not self.ready.get(sl):
                self.ready[sl] = True
                s.capture.restart()

        # spegni gli schermi che nessuno usa
        if time.monotonic() - self.started < STARTUP_GRACE:
            return False
        owners = {d['slot']: dev for dev, d in self.devices.data.items()}
        for sl, st in self.slots.items():
            if not st['active'] or sl in wanted:
                continue
            s = self.sessions.get(owners.get(sl))
            name = s.name if s else f'schermo virtuale {sl + 1}'
            if s:
                await s.stop_capture()
                m = monitors.by_device(st['device'])
                if m:
                    self.devices.update(s.id, posizione=[m['x'], m['y'], m['w'], m['h']])
            self.ready[sl] = False
            n = await asyncio.to_thread(finestre.move_to_primary, st['device'], log)
            if n:
                log(f'{n} finestre spostate dallo schermo di {name} al monitor principale')
            await self.display_change(f'Spengo lo schermo di {name}', monitors.detach_slot, sl)
            return True
        return False

    async def reconciler(self):
        while True:
            try:
                busy = await self.reconcile_once()
            except Exception as e:
                log(f'Errore nella gestione degli schermi: {e!r}')
                busy = False
            awake = any(s.clients for s in self.sessions.values())
            if awake != self.awake:
                keep_pc_awake(awake)
                self.awake = awake
            if busy:
                continue
            self.kick.clear()
            try:
                await asyncio.wait_for(self.kick.wait(), 2)
            except asyncio.TimeoutError:
                pass

    async def keepalive(self):
        while True:
            await asyncio.sleep(0.05)
            self.injector.keepalive()

    async def watch_monitors(self):
        """Se cambia la disposizione degli schermi, riaggancio la cattura di chi ne è toccato."""
        while True:
            await asyncio.sleep(2)
            for s in self.sessions.values():
                # collegato e con il video che parte, ma senza statistiche da 8 s: è troppo carico per mandarle
                if (s.clients and s.capture_task and not s.capture_task.done() and s.sent_frames
                        and time.time() - s.stats.get('ora', time.time()) > 8):
                    s.check_quality()
            outs = monitors.dxgi_outputs()
            for s in self.sessions.values():
                if not s.capture_key:
                    continue
                dev = s.device_name()
                m = dev and monitors.by_device(dev)
                key = m and (dev, outs.get(dev), m['x'], m['y'], m['w'], m['h'])
                s.pad.set_monitor(m)
                if key != s.capture_key:
                    log(f'Schermo di {s.name} cambiato: riavvio la sua cattura')
                    s.pad.release_all()
                    s.capture_key = key
                    s.capture.restart()

    def shutdown(self):
        """Alla chiusura: finestre sul monitor principale e schermi virtuali spenti."""
        try:
            for sl, st in monitors.virtual_slots().items():
                if st['active'] and st['device']:
                    finestre.move_to_primary(st['device'], log)
                    monitors.detach_slot(sl)
                    time.sleep(1)
        except Exception as e:
            log(f'Errore alla chiusura: {e!r}')
        keep_pc_awake(False)


@web.middleware
async def no_cache(request, handler):
    # Safari tiene in cache i file a lungo: così dopo un aggiornamento carica sempre la versione nuova
    resp = await handler(request)
    if request.path != '/ws':
        resp.headers['Cache-Control'] = 'no-cache'
    return resp


async def index(request):
    resp = web.FileResponse(os.path.join(HERE, 'web', 'index.html'))
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


async def touch_icon(request):
    """Percorsi standard dove Safari cerca l'icona per la schermata Home."""
    log(f'Icona per la schermata Home richiesta ({request.path})')
    return web.FileResponse(os.path.join(HERE, 'web', 'icon.png'))


async def ca_certificate(request):
    """Il certificato radice da installare sul dispositivo (una volta sola)."""
    return web.Response(body=certificati.ca_der(), content_type='application/x-x509-ca-cert',
                        headers={'Content-Disposition': 'attachment; filename="Schermo Esteso.crt"'})


async def follow_start():
    """Le app aperte dallo Start compaiono sullo schermo dove hai aperto lo Start."""
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    follower = finestre.StartFollower(lambda msg: loop.call_soon_threadsafe(log, msg))
    try:
        await asyncio.to_thread(follower.run, stop)
    finally:
        stop.set()


async def watch_addresses(ctx, panel, port):
    """Se cambia la rete (es. hotspot del telefono), rifaccio il certificato per il nuovo indirizzo."""
    while True:
        await asyncio.sleep(30)
        ips = lan_ips()
        try:
            changed = await asyncio.to_thread(certificati.reload, ctx, ips)
        except Exception as e:
            log(f'Certificato non aggiornato: {e!r}')
            continue
        panel.urls = [f'https://{ip}:{port}' for ip in ips]
        if changed:
            log('Rete cambiata: sul dispositivo apri ' + '  oppure  '.join(panel.urls))


def on_console_close(app):
    """Chiudere la finestra con la X: Windows concede circa 5 secondi per rimettere in ordine."""
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def handler(event):
        if event in (2, 5, 6):  # chiusura finestra, disconnessione, spegnimento
            app.shutdown()
        return 0

    app._ctrl_handler = handler_type(handler)  # va tenuto in vita
    ctypes.windll.kernel32.SetConsoleCtrlHandler(app._ctrl_handler, 1)


async def main():
    ui_mode = '--ui' in sys.argv  # avviato da avvio.pyw: niente terminale, si chiude con la finestra
    try:
        if os.path.getsize(LOG_FILE) > 2 << 20:
            os.remove(LOG_FILE)
    except OSError:
        pass
    cfg = load_config()
    port = cfg['port']
    local_port = cfg['porta_locale']
    if '--port' in sys.argv:  # per provare una seconda copia senza fermare quella in uso
        port = int(sys.argv[sys.argv.index('--port') + 1])
        local_port = port + 100
    app = App(cfg)
    app.local_port = local_port
    on_console_close(app)
    web_app = web.Application(middlewares=[no_cache, sicurezza.headers])
    web_app.router.add_get('/', index)
    web_app.router.add_get('/certificato.crt', ca_certificate)
    for icon_path in ('/apple-touch-icon.png', '/apple-touch-icon-precomposed.png'):
        web_app.router.add_get(icon_path, touch_icon)
    web_app.router.add_get('/ws', app.ws_handler)
    exchange = Exchange(app, log)
    app.exchange = exchange
    exchange.routes(web_app.router)  # prima dei file statici, che prendono tutto il resto
    app.clipboard = appunti.Clipboard(app, log, cfg.get('appunti', True))
    app.clipboard.routes(web_app.router)
    ips = lan_ips()
    ctx = await asyncio.to_thread(certificati.ssl_context, ips,
                                  lambda alert: log(f'Connessione cifrata rifiutata da un dispositivo: {alert} '
                                                    '(di solito: certificato del PC non attendibile sul dispositivo)'))
    urls = [f'https://{ip}:{port}' for ip in ips]
    panel = Panel(app, log, LOG_LINES, LOG_LISTENERS, FOLDER, urls, ui_mode, exchange)
    panel.on_setting = save_setting
    panel.routes(web_app.router)
    web_app.router.add_static('/', os.path.join(HERE, 'web'))
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port, ssl_context=ctx).start()  # dispositivi: connessione cifrata
    await web.TCPSite(runner, '127.0.0.1', local_port).start()  # solo questo PC

    log('Schermo Esteso è attivo. Sul dispositivo apri in Safari: ' + '  oppure  '.join(urls))
    known = [d['nome'] for d in app.devices.data.values()]
    log('Dispositivi abbinati: ' + (', '.join(known) if known else 'nessuno (il PIN compare qui quando ne colleghi uno)'))
    log(f'Cartella dei file condivisi con i dispositivi: {FOLDER}')

    tasks = [asyncio.create_task(app.keepalive()), asyncio.create_task(app.watch_monitors()),
             asyncio.create_task(app.reconciler()), asyncio.create_task(exchange.watch()),
             asyncio.create_task(app.clipboard.watch()),
             asyncio.create_task(panel.watch_window()), asyncio.create_task(watch_addresses(ctx, panel, port)),
             asyncio.create_task(follow_start())]
    stopper = asyncio.create_task(app.stop.wait())
    try:
        await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            if t.done() and not t.cancelled() and t.exception():
                log(f'Errore interno: {t.exception()!r}')
    finally:
        for t in [stopper, *tasks]:
            t.cancel()
        for s in app.sessions.values():
            s.capture.stop()
        app.injector.release_all()
        await asyncio.to_thread(app.shutdown)
        log('Chiuso.')
        await runner.cleanup()


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
