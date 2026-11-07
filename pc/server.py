"""Screen Extender: use iPad, iPhone and other devices as extra Windows screens.

Start: python server.py  (or "Avvia.bat"), then on the device open in Safari
the address shown. A new device must enter the PIN that appears here.

Every device has its own virtual screen: it turns on when the device connects
and turns off 15 s after it disconnects. Before turning it off, its windows are
moved to the main monitor, below the ones already open.
"""
import asyncio
import collections
import ctypes
import ctypes.wintypes
import json
import os
import socket
import sys
import threading
import time

from aiohttp import WSMsgType, web

import appunti
import bordi
import certificati
import collegamenti
import installa
import qualita
import riposo
import sicurezza
import trascina
import finestre
import lingua
import monitors
import vdd
from capture import Capture
from devices import MAX_DEVICES, Devices
import inject
from inject import Injector, Pad
from pannello import Panel
from scambio import FOLDER, Exchange

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')  # personal data (outside git)
os.makedirs(DATA, exist_ok=True)
CONFIG = os.path.join(DATA, 'config.json')
DEVICES = os.path.join(DATA, 'dispositivi.json')
DEFAULTS = {
    'port': 8770,         # port for the devices (HTTPS)
    'porta_locale': 8769,  # port for this PC only (control window, Send to), unencrypted
    'quality': 1,       # starting level for new devices (0 Best .. 3 Light)
    'bufsize': '1M',    # small buffer = less delay
    'encoder': 'auto',  # auto | qsv | x264
    'appunti': True,    # clipboard shared between the PC and the devices
    'spinta_bordo': True,  # push the mouse against a free edge: the device screen goes there
    'lingua': None,     # 'it' or 'en' (for everyone: PC window and devices); None = the Windows one
}
QUALITY_LABELS = ['Massima', 'Fluida', 'Media', 'Leggera']
MAX_QUEUE = 4        # frames queued per client beyond which I skip to the next full frame
                     # (few: if the Wi-Fi slows down it's better to jump to the latest picture than to pile up delay)
SEND_BUFFER = 256 * 1024  # Windows network buffer for each device: small, so no hidden queue
GRACE = 15           # seconds after the app is closed before turning its screen off
STARTUP_GRACE = 20   # at startup wait a bit for the devices before turning off the screens nobody uses
DETACH_RETRY = 60     # seconds between attempts if a screen can't be turned off
ASK_TIME = 15         # seconds to answer "it's here" when more than one device is connected
SIDE_TEXT = {'destra': 'a destra', 'sinistra': 'a sinistra', 'sopra': 'sopra', 'sotto': 'sotto'}
LOW_BATTERY = 15      # % below which I warn that the device battery is almost empty
MIN_HEIGHT_PT = 768    # minimum screen height for Windows, in points (pixels / scaling)
DISPLAY_GAP = 3      # minimum seconds between two screen configuration changes
RELOAD_WAIT = 8      # seconds to wait after restarting the driver


LOG_LINES = collections.deque(maxlen=3000)  # latest lines, for the control window
LOG_LISTENERS = set()
LOG_FILE = os.path.join(DATA, 'server.log')


def log(msg, chi=None):
    """Console line. chi = device (or 'PC') that performed the action: shown as [iPad]."""
    if chi:
        msg = f'[{chi}] {msg}'
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)  # without a terminal (pythonw) nothing is printed
    LOG_LINES.append(line)
    for fn in list(LOG_LISTENERS):
        fn(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(lingua.line(line) + '\n')  # the window receives Italian and translates it itself
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding='utf-8') as f:
            cfg.update(json.load(f))
    for k in ('fps', 'bitrate', 'maxrate', 'gop', 'pin'):  # old keys no longer used
        cfg.pop(k, None)
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return cfg


def save_setting(key, value):
    """Changes a single setting in config.json (from the PC window)."""
    cfg = load_config()
    cfg[key] = value
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


async def update_shortcuts(lang):
    """Start menu and Send to shortcuts, created only when missing (see collegamenti.py). Never stops the program."""
    if '--port' in sys.argv:  # second copy for testing: the shortcuts stay with the one in use
        return
    try:
        changed = await asyncio.to_thread(collegamenti.sync, lang)
    except Exception as e:
        log(f'Collegamenti del menu Start non creati: {e!r}')
        return
    if changed:
        log(f'Collegamenti creati (menu Start e Invia a): {collegamenti.NAME}')


SETUP_PARTS = {'driver': 'driver dello schermo virtuale', 'firewall': 'regole del firewall'}


async def first_setup():
    """Driver and firewall: if one is missing, asks for administrator permission and sets it up (see installa.py)."""
    if '--port' in sys.argv:
        return
    try:
        todo = await asyncio.to_thread(installa.missing)
        if not todo:
            return
        log('Prima configurazione: serve il permesso di amministratore per ' +
            ', '.join(SETUP_PARTS[x] for x in todo))
        code = await asyncio.to_thread(installa.run)
        if code is None:
            log('Permesso negato: lo richiedo al prossimo avvio. Senza driver non ci sono schermi virtuali, '
                'senza firewall i dispositivi non si collegano.')
            return
        left = await asyncio.to_thread(installa.missing)
    except Exception as e:
        log(f'Configurazione non riuscita: {e!r}')
        return
    for x in todo:
        if x in left:
            log(f'Non riuscito: {SETUP_PARTS[x]}. Dettagli nel file data\\setup.log')
        else:
            log(f'Fatto: {SETUP_PARTS[x]}')


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


# --- PC always on while a device is connected ----------------------
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2


def keep_pc_awake(on):
    # if the PC screens turn off, Windows turns off the virtual ones too
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
            # the network fell behind: drop the queue and restart from the next full frame,
            # so the delay doesn't pile up
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
    """A paired device: its virtual screen, its video capture, its touches."""

    def __init__(self, app, dev_id):
        self.app = app
        self.id = dev_id
        self.clients = set()
        self.last_seen = time.monotonic()
        self.config_msg = None
        self.gop = []  # frames since the last full frame, for whoever joins halfway
        self.pad = Pad(app.injector)
        self.monitor = None
        self.capture_key = None
        settings = dict(app.cfg)
        settings['quality'] = self.info.get('qualita', app.cfg['quality'])
        self.capture = Capture(settings, self.target, self.on_config, self.on_frame,
                               lambda m: log(m, self.name))
        self.capture_task = None
        self.stats = {}
        self.sent_frames = 0  # for the performance view in the PC window
        self.sent_bytes = 0
        self.auto = qualita.AutoQuality(time.monotonic())
        self.judged = (time.monotonic(), 0, 0)  # (time, frames sent, skipped) at the last judgement

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
        """\\\\.\\DISPLAYn of this device's screen, if it's on."""
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
            # the whole group at once (without the queue limit): the device decodes it quickly
            c.queue.extend(self.gop)
            c.need_key = False
            c.wake.set()

    def notify(self, msg):
        """Message to the device, ahead of the queued video: it arrives right away even on a congested network."""
        text = json.dumps(msg)
        for c in self.clients:
            c.queue.appendleft(text)
            c.wake.set()

    def set_area(self, w, h):
        """Tailored resolution: the virtual screen takes exactly the usable area of the device
        (e.g. the whole iPad screen except the bar with time and battery), so no black bars."""
        try:
            w, h = int(w), int(h)
        except (TypeError, ValueError):
            return
        if not (480 <= w <= 4096 and 480 <= h <= 4096 and 0.4 <= w / h <= 2.5):
            return  # sensible sizes, landscape or portrait (device rotated)
        w, h = w // 2 * 2, h // 2 * 2  # even, as the H.264 encoder requires
        if list(self.info.get('risoluzione', [])) == [w, h]:
            return
        log(f'nuova area utile {w}x{h}: adatto lo schermo virtuale', self.name)
        self.app.devices.update(self.id, risoluzione=[w, h], configurato=False)
        self.app.kick.set()

    def set_quality(self, level, source, auto=False):
        """Quality (frames per second and resolution). The PC remembers it: the device can change it
        from its menu, or the PC, which enforces it even if the device is too slow to respond.
        Chosen by hand = the maximum automatic quality can go back up to."""
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
            c.queue.appendleft(msg)  # ahead of the queued video: it arrives right away even if the network is congested
            c.wake.set()
        if level != old:
            log(f'qualità {QUALITY_LABELS[level]} ({source}): riavvio la cattura', self.name)
            self.capture.restart()

    def check_battery(self, level, charging):
        """Device battery below 15% and not charging: one warning in the console (only one, until
        it goes back above 20% or gets plugged in)."""
        if not isinstance(level, int):
            return
        low = level <= LOW_BATTERY and not charging
        if low and not getattr(self, 'battery_warned', False):
            self.battery_warned = True
            log(f'batteria al {level}%: collegalo alla corrente, se si spegne il suo schermo si chiude', self.name)
        elif charging or level > LOW_BATTERY + 5:
            self.battery_warned = False

    async def select_word(self, c, x, y, delivered=False):
        """Double tap on the device: selects the word. In browsers with a mouse double click (with touch the word isn't
        selected in browsers, which don't do it on touch). Wait a moment: right after
        a touch Edge ignores mouse clicks."""
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return
        pt = self.pad.point(x, y)
        if pt and finestre.browser_at(*pt):
            # Edge counts mouse clicks together with touches from less than ~0.5 s ago: wait only as long as needed
            await asyncio.sleep(max(0.0, 0.55 - (time.monotonic() - self.pad.last_touch)))
            for _ in range(2):
                self.pad.mouse('click', 0, x, y)
                await asyncio.sleep(0.03)
        elif not delivered:
            # classic programs (Notepad, Word…): the Windows double tap already selects the word
            self.pad.touch('d', 'parola', x, y)
            await asyncio.sleep(0.03)
            self.pad.touch('u', 'parola', x, y)
            await asyncio.sleep(0.15)
        else:
            await asyncio.sleep(0.15)
        c.queue.appendleft(json.dumps({'t': 'parola_ok'}))  # now the device shows Copy/Paste
        c.wake.set()

    def check_quality(self, stats=None):
        """Automatic quality: called on every device statistic (about every 2 s) and, if the
        device is silent, from the periodic check."""
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
            if use:  # to the device: whether in this stroke the Pencil writes or acts as a finger (no ink)
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
                # never the content in the console: for passwords not even the length
                log('password digitata sul PC' if d.get('segreto') else f'testo digitato sul PC ({len(text)} caratteri)',
                    self.name)
        elif t == 'tasto':
            inject.press(str(d.get('k', '')))
        elif t == 'deposita':
            asyncio.create_task(self.app.drop_file(self, d.get('nome', ''), d.get('x'), d.get('y')))
        elif t == 'parola':
            asyncio.create_task(self.select_word(c, d.get('x'), d.get('y'), bool(d.get('gia'))))
        elif t == 'qui':
            self.app.edge_answer(self)
        elif t == 'diag':
            d.pop('t', None)
            log(f'info: {json.dumps(d, ensure_ascii=False)}', self.name)
        elif t == 'lingua':  # chosen from the device menu: applies to everyone
            self.app.set_language(d.get('lingua'), self.name)
        elif t == 'stats':
            if d.get('pausa'):  # the device was paused (app switch, Control Center): data not valid
                self.stats['ora'] = time.time()
                self.auto.changed(time.monotonic())
                return
            self.stats = {k: d.get(k) for k in ('fps', 'dec', 'rtt', 'mbit', 'queue', 'render', 'q', 'batt', 'carica')}
            self.check_battery(d.get('batt'), bool(d.get('carica')))
            self.stats['ora'] = time.time()  # shown in the PC window, not in the console
            self.check_quality(d)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.devices = Devices(DEVICES)
        self.injector = Injector(log)
        self.sessions = {}
        self.slots = {}     # state of the virtual screens (from monitors.virtual_slots)
        self.ready = {}     # slot -> True when the screen is on and configured
        self.started = time.monotonic()
        self.last_change = 0.0
        self.awake = False
        self.kick = asyncio.Event()
        self.stop = asyncio.Event()  # orderly shutdown (from the PC window)
        self.pair_fails = []   # times of the wrong PINs (all devices)
        self.pair_block = 0.0  # pairing blocked until this time
        self.reposition = {}  # slot -> (x, y) to restore after a driver restart
        self.mode_tries = {}  # slot -> attempts to put the tailored resolution back
        self.edge_ask = None  # (side, monitor, point, deadline): "is it here?" question to the devices
        self.detach_wait = {}  # slot -> time of the next attempt, after a failed turn-off
        self.panel = None      # control window (to notify it when the language changes)

    def set_language(self, lang, who='PC'):
        """Language chosen from the PC window or a device: remember it and send it to everyone."""
        if lang not in lingua.LINGUE or lang == lingua.current:
            return
        lingua.use(lang)
        save_setting('lingua', lang)
        log(f'Lingua: {lingua.NOMI[lang]}', who)
        msg = {'t': 'lingua', 'lingua': lang}
        for s in self.sessions.values():
            if s.clients:
                s.notify(msg)
        if self.panel:
            self.panel.send_all(msg)

    def session(self, dev_id):
        s = self.sessions.get(dev_id)
        if not s:
            s = self.sessions[dev_id] = Session(self, dev_id)
        return s

    # ---- connection and pairing ----
    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=1 << 20, compress=False)
        await ws.prepare(request)
        # Windows on its own can queue megabytes of video toward a slow Wi-Fi (delay the server doesn't
        # see): with a small buffer the server notices right away and jumps to the latest frames
        tr = request.transport
        sock = tr.get_extra_info('socket') if tr else None
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        if tr is not None:
            try:
                tr.set_write_buffer_limits(high=SEND_BUFFER // 2)
            except (AttributeError, ValueError, RuntimeError):
                pass
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
        s.stats = {'ora': time.time()}  # from here I count the device's silence
        s.auto.changed(time.monotonic())  # just reconnected (e.g. back from the app switcher): don't judge right away
        s.last_seen = time.monotonic()
        self.devices.update(dev_id, visto=time.strftime('%Y-%m-%d %H:%M'), ip=request.remote)
        log(f'collegato da {request.remote}', s.name)
        self.kick.set()
        if not riposo.asleep():
            s.start_capture()
        s.send_gop(c)
        c.queue.appendleft(json.dumps({'t': 'quality', 'level': s.capture.level}))  # the PC remembers the quality
        # first, before the video; with the PC addresses: if it changes network (e.g. hotspot) the app tries them
        port = self.cfg.get('port', 8770)
        c.queue.appendleft(json.dumps({'t': 'lingua', 'lingua': lingua.current}))
        if riposo.asleep():
            c.queue.appendleft(json.dumps({'t': 'riposo'}))  # right after "hello"
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
                except Exception as e:  # a bad message must not drop the connection
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
        """Pairing with a PIN. Returns True if the device was accepted.
        A device not yet paired receives the PC language but can't change it."""
        await ws.send_str(json.dumps({'t': 'lingua', 'lingua': lingua.current}))
        await ws.send_str(json.dumps({'t': 'pair'}))
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            if d.get('t') == 'hello':  # the device introduces itself: show the PIN on the PC
                who = sicurezza.clean_text(d.get('nome')) or 'Dispositivo'
                if time.monotonic() < self.pair_block:
                    await ws.send_str(json.dumps({'t': 'pinerr', 'bloccato': int(self.pair_block - time.monotonic())}))
                    return False
                got = self.devices.pin_for(dev_id, who)
                if not got:  # too many pairing requests open at once
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
                # limit for the whole PC: someone guessing by opening many requests at once
                # is stopped anyway
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
                w, h = max(w, h), min(w, h)  # landscape
                if not (640 <= w <= 4096 and 480 <= h <= 4096):
                    w, h = 2048, 1536
                w, h = w // 2 * 2, h // 2 * 2  # even sizes, required by the H.264 encoder
                name = sicurezza.clean_text(d.get('nome')) or 'Dispositivo'
                token, replaced = self.devices.pair(dev_id, name, (w, h), dpr, remote)
                for old_id in replaced:  # the same iPad already paired (e.g. from Safari): close the old entry
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
            await asyncio.sleep(1)  # slows down whoever is guessing
        return False

    async def open_here(self, s, name):
        """Opens a shared file on the PC and brings its window to the device screen."""
        path = self.exchange.path_of(name)
        if not path:
            log(f'vuole aprire {name}, ma il file non è più nella cartella', s.name)
            return
        if not sicurezza.safe_to_open(path):
            # programs and scripts coming from a device are never run on the PC
            log(f'vuole aprire {name} sul PC: rifiutato, questo tipo di file potrebbe essere un programma', s.name)
            return
        m = s.monitor
        if not m or not s.capture_key:
            log(f'vuole aprire {name}, ma il suo schermo non è acceso', s.name)
            return
        log(f'aperto file {name} sul PC, sul suo schermo', s.name)
        await asyncio.to_thread(finestre.open_on, path, m, log, 12, os.path.join(DATA, '.edge-profile'))

    async def drop_file(self, s, name, nx, ny):
        """File dragged from the device onto its screen: on the PC I do a real drag at the same
        point, so the program there decides (File Explorer and the desktop copy it, Obsidian puts it
        in the vault folder, Word inserts it…). If nobody takes it, I open it with its program."""
        path = self.exchange.path_of(str(name))
        m = s.monitor
        pt = s.pad.point(nx, ny) if isinstance(nx, (int, float)) and isinstance(ny, (int, float)) else None
        if not path or not m or not s.capture_key or not pt:
            return
        under = finestre.process_name(finestre.user32.WindowFromPoint(finestre.wintypes.POINT(*pt))) or 'il desktop'
        if not sicurezza.safe_to_open(path):
            log(f'ha trascinato {name}: è nella cartella condivisa, ma programmi e script non li trascino nei '
                'programmi del PC', s.name)
            return
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
        await asyncio.to_thread(finestre.open_on, path, m, log, 12, os.path.join(DATA, '.edge-profile'))

    # ---- screens ----
    async def display_change(self, what, fn, *args):
        """One configuration change at a time, spaced out: changes close together can freeze Windows."""
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
        """The driver must have enough screens and all the devices' resolutions.
        Returns True if it had to restart it."""
        count, res = vdd.read_settings()
        need = max([count] + [s + 1 for s in wanted])
        sizes = [tuple(d['risoluzione']) for d in self.devices.data.values() if d.get('risoluzione')]
        missing = [r for r in sizes if r not in res]
        if need == count and not missing:
            return False
        # the restart shuffles the positions: note where the screens that are on are now
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
        """One step toward the desired state: turns on, configures or turns off at most one screen."""
        self.refresh_slots()
        wanted = {s.slot: s for s in self.sessions.values() if s.slot is not None and s.wants_screen()}

        # does the driver need a restart (new device or new resolution)?
        if any(sl not in self.slots for sl in wanted):
            if await self.ensure_driver(wanted):
                for sl in list(self.ready):
                    self.ready[sl] = False
                return True

        # turn on
        for sl, s in wanted.items():
            st = self.slots.get(sl)
            if st and not st['active']:
                geo = s.info.get('posizione')
                r = await self.display_change(f'Accendo lo schermo di {s.name}', monitors.attach_slot, sl, geo and tuple(geo))
                if r != 0:
                    log(f'Accensione non riuscita (codice {r})')
                return True

        # configure (only the first time for each device: resolution, then scaling)
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
                    if await self.ensure_driver(wanted):  # new resolution: the driver must know it
                        for k in list(self.ready):
                            self.ready[k] = False
                        return True
                    log(f'Risoluzione {w}x{h} non disponibile per {s.name}: tengo {m["w"]}x{m["h"]}')
                # scaling is chosen only for just-paired devices: for the others
                # I keep what's set in Windows
                if s.info.get('scala_da_impostare'):
                    scale = min(200, max(100, round(s.info.get('dpr', 1) * 100 / 25) * 25))
                    # short screens (iPhone in landscape): at least ~768 points tall, otherwise app
                    # menus go off the screen
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
                # the tailored resolution can get lost (driver restart, Windows changes): put it back
                want = tuple(s.info.get('risoluzione') or (m['w'], m['h']))
                if (m['w'], m['h']) != want and self.mode_tries.get(sl, 0) < 2                         and want in monitors.list_modes(st['device']):
                    self.mode_tries[sl] = self.mode_tries.get(sl, 0) + 1
                    await self.display_change(f'Rimetto la risoluzione {want[0]}x{want[1]} per {s.name}',
                                              monitors.set_mode, st['device'], *want)
                    return True
                if (m['w'], m['h']) == want:
                    self.mode_tries.pop(sl, None)
                # position always up to date: on the next turn-on it goes back where you actually use it
                geo = [m['x'], m['y'], m['w'], m['h']]
                if s.info.get('posizione') != geo:
                    self.devices.update(s.id, posizione=geo)
            if not self.ready.get(sl):
                self.ready[sl] = True
                s.capture.restart()

        # turn off the screens nobody uses
        if time.monotonic() - self.started < STARTUP_GRACE:
            return False
        owners = {d['slot']: dev for dev, d in self.devices.data.items()}
        for sl, st in self.slots.items():
            if not st['active'] or sl in wanted:
                self.detach_wait.pop(sl, None)
                continue
            if time.monotonic() < self.detach_wait.get(sl, 0):
                continue  # turn-off just failed: retry later, without flooding the console
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
            failed = sl in self.detach_wait
            if failed:  # new attempt after an error: quietly
                r = await asyncio.to_thread(monitors.detach_slot, sl)
                self.last_change = time.monotonic()
            else:
                r = await self.display_change(f'Spengo lo schermo di {name}', monitors.detach_slot, sl)
            if r != 0:
                # e.g. laptop lid closed: the virtual screen is the only one on and I never turn it off
                self.detach_wait[sl] = time.monotonic() + DETACH_RETRY
                if not failed:
                    why = ("è l'unico schermo acceso (coperchio del portatile chiuso?): lo lascio acceso"
                           if r == -1 else f'Windows ha risposto con il codice {r}')
                    log(f'Schermo di {name} non spento: {why}. Riprovo ogni minuto')
                continue
            if failed:
                log(f'Schermo di {name} spento')
            self.detach_wait.pop(sl, None)
            return True
        return False

    async def reconciler(self):
        while True:
            await riposo.wait_awake()  # PC asleep: no screen to turn on or off
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

    # ---- sleep: the PC is asleep (lid closed, standby) ----
    async def watch_rest(self):
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        watcher = riposo.Watcher(loop, self.on_rest, lambda msg: loop.call_soon_threadsafe(log, msg))
        try:
            await asyncio.to_thread(watcher.run, stop)
        finally:
            stop.set()

    def on_rest(self, sleeping):
        """The PC goes to sleep: stop the video and tell the devices, which stop keeping their screen on.
        It wakes up: restart. Periodic checks stop and resume by themselves (riposo.nap)."""
        if sleeping:
            log('PC in sospensione: fermo il video e i controlli finché non si sveglia')
            for s in self.sessions.values():
                if s.clients:
                    s.notify({'t': 'riposo'})
                asyncio.ensure_future(s.stop_capture())
        else:
            log('PC sveglio: riprendo')
            for s in self.sessions.values():
                if s.clients:
                    s.notify({'t': 'sveglio'})
                    s.start_capture()
        self.kick.set()

    # ---- mouse push against the edge (like Universal Control on a Mac) ----
    async def watch_edges(self):
        if not self.cfg.get('spinta_bordo', True):
            return
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def pushed(side, mon, pt):  # from the mouse thread
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.on_edge_push(side, mon, pt)))

        watcher = bordi.EdgeWatcher(pushed, lambda msg: loop.call_soon_threadsafe(log, msg))
        try:
            await asyncio.to_thread(watcher.run, stop)
        finally:
            stop.set()

    def screens_in_use(self):
        return [s for s in self.sessions.values() if s.clients and self.ready.get(s.slot) and s.device_name()]

    async def on_edge_push(self, side, mon, pt):
        """You pushed the mouse against a free edge: the screen of the device that sits there moves to that side.
        With a single device connected it's that one; with more than one I ask them all and move the one that answers."""
        on = self.screens_in_use()
        if not on:
            return
        if len(on) == 1:
            await self.move_screen(on[0], side, mon, pt)
            return
        self.edge_ask = (side, mon, pt, time.monotonic() + ASK_TIME)
        log(f'Spinta del mouse {SIDE_TEXT[side]}: chiedo ai dispositivi quale si trova lì')
        for s in on:
            s.notify({'t': 'dove', 'lato': SIDE_TEXT[side], 'secondi': ASK_TIME})

    def edge_answer(self, s):
        ask, self.edge_ask = self.edge_ask, None
        if not ask or time.monotonic() > ask[3]:
            return
        for o in self.screens_in_use():
            if o is not s:
                o.notify({'t': 'dove_fine'})
        asyncio.ensure_future(self.move_screen(s, *ask[:3]))

    async def move_screen(self, s, side, mon, pt):
        dev = s.device_name()
        m = dev and monitors.by_device(dev)
        if not m:
            return
        others = [o for o in monitors.list_monitors() if o['device'] != dev]
        pos = bordi.place(side, mon, pt, (m['w'], m['h']), others)
        where = SIDE_TEXT[side]
        if not pos:
            log(f"Spinta del mouse {where}: non c'è posto per lo schermo di {s.name}")
            return
        if (m['x'], m['y']) == pos:
            return
        r = await self.display_change(f'Sposto lo schermo di {s.name} {where} del monitor (spinta del mouse)',
                                      monitors.set_slot_position, s.slot, *pos)
        if r != 0:
            log(f'Spostamento non riuscito (codice {r})')
            return
        await self.pointer_into(dev, side, pt)
        s.notify({'t': 'avviso', 'testo': lingua.t(f'Schermo spostato {where} del PC')})
        self.kick.set()  # the new position is remembered for the next turn-ons

    async def pointer_into(self, dev, side, pt):
        """The pointer goes into the device screen, from the side you pushed.
        Windows applies the new layout with a slight delay and then puts the pointer back in the center
        of the main monitor: for a second and a half I check and, if it moved it, I put it back."""
        user32 = ctypes.windll.user32
        cur = ctypes.wintypes.POINT()
        end = time.monotonic() + 1.5
        placed, steady = None, 0
        while time.monotonic() < end and steady < 5:  # inside for half a second in a row: Windows is done
            n = monitors.by_device(dev)
            if n:
                x = n['x'] + 20 if side == 'destra' else n['x'] + n['w'] - 21 if side == 'sinistra' else pt[0]
                y = n['y'] + 20 if side == 'sotto' else n['y'] + n['h'] - 21 if side == 'sopra' else pt[1]
                x = max(n['x'] + 1, min(n['x'] + n['w'] - 2, x))
                y = max(n['y'] + 1, min(n['y'] + n['h'] - 2, y))
                user32.GetCursorPos(ctypes.byref(cur))
                inside = n['x'] <= cur.x < n['x'] + n['w'] and n['y'] <= cur.y < n['y'] + n['h']
                if placed is None or not inside:
                    user32.SetCursorPos(x, y)
                    placed, steady = (x, y), 0
                else:
                    steady += 1
            await asyncio.sleep(0.1)

    async def keepalive(self):
        while True:
            busy = self.injector.keepalive()
            # with a finger or the pen down, every 50 ms; otherwise checking 4 times a second is enough
            await riposo.nap(0.05 if busy else 0.25)

    async def watch_monitors(self):
        """If the screen layout changes, reattach the capture of whoever is affected."""
        while True:
            await riposo.nap(2)
            for s in self.sessions.values():
                # connected and with video starting, but no statistics for 8 s: it's too busy to send them
                if (s.clients and s.capture_task and not s.capture_task.done() and s.sent_frames
                        and time.time() - s.stats.get('ora', time.time()) > 8):
                    s.check_quality()
            if not any(s.capture_key for s in self.sessions.values()):
                continue  # no screen in use: nothing to check
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
        """On shutdown: windows to the main monitor and virtual screens off."""
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
    # Safari caches files for a long time: this way after an update it always loads the new version
    resp = await handler(request)
    if request.path != '/ws':
        resp.headers['Cache-Control'] = 'no-cache'
    return resp


async def index(request):
    resp = web.FileResponse(os.path.join(HERE, 'web', 'index.html'))
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


async def touch_icon(request):
    """Standard paths where Safari looks for the Home Screen icon."""
    log(f'Icona per la schermata Home richiesta ({request.path})')
    return web.FileResponse(os.path.join(HERE, 'web', 'icon.png'))


async def ca_certificate(request):
    """The root certificate to install on the device (only once)."""
    return web.Response(body=certificati.ca_der(), content_type='application/x-x509-ca-cert',
                        headers={'Content-Disposition': 'attachment; filename="Screen Extender.crt"'})


async def follow_start():
    """Apps opened from Start appear on the screen where you opened Start."""
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    follower = finestre.StartFollower(lambda msg: loop.call_soon_threadsafe(log, msg))
    try:
        await asyncio.to_thread(follower.run, stop)
    finally:
        stop.set()


async def watch_addresses(ctx, panel, port):
    """If the network changes (e.g. phone hotspot), remake the certificate for the new address."""
    while True:
        await riposo.nap(30)
        ips = lan_ips()
        try:
            changed = await asyncio.to_thread(certificati.reload, ctx, ips)
        except Exception as e:
            log(f'Certificato non aggiornato: {e!r}')
            continue
        panel.urls = [f'https://{ip}:{port}' for ip in ips]
        if changed:
            log('Rete cambiata: certificato del PC aggiornato da solo (sui dispositivi non serve reinstallare niente). '
                'Indirizzi: ' + '  oppure  '.join(panel.urls))


def on_console_close(app):
    """Closing the window with the X: Windows allows about 5 seconds to tidy up."""
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def handler(event):
        if event in (2, 5, 6):  # window closed, logoff, shutdown
            app.shutdown()
        return 0

    app._ctrl_handler = handler_type(handler)  # must be kept alive
    ctypes.windll.kernel32.SetConsoleCtrlHandler(app._ctrl_handler, 1)


async def main():
    ui_mode = '--ui' in sys.argv  # started by avvio.pyw: no terminal, it closes with the window
    try:
        if os.path.getsize(LOG_FILE) > 2 << 20:
            os.remove(LOG_FILE)
    except OSError:
        pass
    cfg = load_config()
    lingua.use(cfg.get('lingua') or lingua.default())
    port = cfg['port']
    local_port = cfg['porta_locale']
    if '--port' in sys.argv:  # to try a second copy without stopping the one in use
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
    exchange.routes(web_app.router)  # before the static files, which take everything else
    app.clipboard = appunti.Clipboard(app, log, cfg.get('appunti', True))
    app.clipboard.routes(web_app.router)
    ips = lan_ips()
    ctx = await asyncio.to_thread(certificati.ssl_context, ips,
                                  lambda alert: log(f'Connessione cifrata rifiutata da un dispositivo: {alert} '
                                                    '(di solito: certificato del PC non attendibile sul dispositivo)'))
    urls = [f'https://{ip}:{port}' for ip in ips]
    panel = Panel(app, log, LOG_LINES, LOG_LISTENERS, FOLDER, urls, ui_mode, exchange)
    panel.on_setting = save_setting
    app.panel = panel
    panel.routes(web_app.router)
    web_app.router.add_static('/', os.path.join(HERE, 'web'))
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port, ssl_context=ctx).start()  # devices: encrypted connection
    await web.TCPSite(runner, '127.0.0.1', local_port).start()  # this PC only

    log('Screen Extender è attivo. Sul dispositivo apri in Safari: ' + '  oppure  '.join(urls))
    known = [d['nome'] for d in app.devices.data.values()]
    log('Dispositivi abbinati: ' + (', '.join(known) if known else 'nessuno (il PIN compare qui quando ne colleghi uno)'))
    log(f'Cartella dei file condivisi con i dispositivi: {FOLDER}')
    await update_shortcuts(lingua.current)
    asyncio.ensure_future(first_setup())  # in the background: the window is already usable while it runs

    tasks = [asyncio.create_task(app.keepalive()), asyncio.create_task(app.watch_monitors()),
             asyncio.create_task(app.reconciler()), asyncio.create_task(exchange.watch()),
             asyncio.create_task(app.clipboard.watch()),
             asyncio.create_task(panel.watch_window()), asyncio.create_task(watch_addresses(ctx, panel, port)),
             asyncio.create_task(follow_start()), asyncio.create_task(app.watch_edges()),
             asyncio.create_task(app.watch_rest())]
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
