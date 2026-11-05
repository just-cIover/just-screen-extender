"""Appunti condivisi tra il PC e i dispositivi (testo e immagini).

- PC -> dispositivi: controllo il numero di sequenza degli appunti di Windows; quando cambia mando il
  testo (o avviso che c'è un'immagine, che i dispositivi scaricano da /api/appunti/immagine).
- Dispositivo -> PC: testo dal WebSocket ({t: "appunti"}), immagini con POST /api/appunti/immagine.
- Riservatezza: niente contenuto nella console; salto gli appunti che i gestori di password segnano
  come da non condividere (gli stessi formati che rispetta la cronologia degli appunti di Windows).
"""
import asyncio
import ctypes
import io
import json
from ctypes import wintypes

from aiohttp import web

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

CF_UNICODETEXT, CF_DIB = 13, 8
GMEM_MOVEABLE = 0x2
MAX_TEXT = 1_000_000        # caratteri
MAX_IMAGE = 25 * 1024 * 1024  # byte

user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

# formati usati da KeePass, Bitwarden, 1Password… per dire "non copiare altrove"
EXCLUDE = user32.RegisterClipboardFormatW('ExcludeClipboardContentFromMonitorProcessing')
NO_HISTORY = user32.RegisterClipboardFormatW('CanIncludeInClipboardHistory')
NO_CLOUD = user32.RegisterClipboardFormatW('CanUploadToCloudClipboard')


class _Open:
    """Apre gli appunti (riprovando: un altro programma può tenerli aperti per un attimo)."""

    def __enter__(self):
        import time
        for _ in range(20):
            if user32.OpenClipboard(None):
                return self
            time.sleep(0.02)
        raise OSError('appunti occupati da un altro programma')

    def __exit__(self, *exc):
        user32.CloseClipboard()


def sequence():
    return user32.GetClipboardSequenceNumber()


def _dword_zero(fmt):
    h = user32.GetClipboardData(fmt)
    if not h:
        return False
    p = kernel32.GlobalLock(h)
    try:
        return bool(p) and kernel32.GlobalSize(h) >= 4 and ctypes.c_uint32.from_address(p).value == 0
    finally:
        kernel32.GlobalUnlock(h)


def read():
    """('testo', str) | ('immagine', png bytes) | ('privato', None) | (None, None)."""
    with _Open():
        if user32.IsClipboardFormatAvailable(EXCLUDE):
            return 'privato', None
        for fmt in (NO_HISTORY, NO_CLOUD):
            if user32.IsClipboardFormatAvailable(fmt) and _dword_zero(fmt):
                return 'privato', None
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            p = kernel32.GlobalLock(h)
            try:
                text = ctypes.wstring_at(p) if p else ''
            finally:
                kernel32.GlobalUnlock(h)
            return ('testo', text[:MAX_TEXT]) if text else (None, None)
        has_image = user32.IsClipboardFormatAvailable(CF_DIB)
    if has_image:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is not None and not isinstance(img, list):
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            if buf.tell() <= MAX_IMAGE:
                return 'immagine', buf.getvalue()
    return None, None


def _set(fmt, data):
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    with _Open():
        user32.EmptyClipboard()
        if not user32.SetClipboardData(fmt, h):
            kernel32.GlobalFree(h)
            raise OSError('Windows non ha accettato gli appunti')


def write_text(text):
    _set(CF_UNICODETEXT, (text + '\0').encode('utf-16-le'))


def write_image(data):
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGBA')
    if img.mode == 'RGBA':  # nel formato DIB classico la trasparenza si perde: sfondo bianco
        bg = Image.new('RGB', img.size, 'white')
        bg.paste(img, mask=img.split()[3])
        img = bg
    buf = io.BytesIO()
    img.save(buf, 'BMP')
    _set(CF_DIB, buf.getvalue()[14:])  # DIB = BMP senza l'intestazione del file


class Clipboard:
    """Sincronizza gli appunti. app = l'App del server (dispositivi, sessioni), log = console."""

    def __init__(self, app, log, enabled=True):
        self.app = app
        self.log = log
        self.enabled = enabled
        self.seq = sequence()
        self.ours = set()      # numeri di sequenza scritti da noi (da non rimandare indietro)
        self.image = None      # ultima immagine copiata sul PC (png), per i dispositivi
        self.image_id = 0

    def _broadcast(self, msg, skip=None):
        text = json.dumps(msg)
        for s in self.app.sessions.values():
            if s.id == skip:
                continue
            for c in s.clients:
                c.queue.appendleft(text)
                c.wake.set()

    async def watch(self):
        while True:
            await asyncio.sleep(0.5)
            seq = sequence()
            if seq == self.seq:
                continue
            self.seq = seq
            if seq in self.ours or not self.enabled:
                continue
            if not any(s.clients for s in self.app.sessions.values()):
                continue
            try:
                kind, data = await asyncio.to_thread(read)
            except Exception as e:
                self.log(f'Appunti del PC non letti: {e}')
                continue
            if kind == 'privato':
                self.log('appunti segnati come privati (es. una password): non li mando ai dispositivi', 'PC')
            elif kind == 'testo':
                self._broadcast({'t': 'appunti', 'tipo': 'testo', 'testo': data})
                self.log(f'appunti copiati sui dispositivi (testo, {len(data)} caratteri)', 'PC')
            elif kind == 'immagine':
                self.image, self.image_id = data, seq
                self._broadcast({'t': 'appunti', 'tipo': 'immagine', 'id': seq, 'dim': len(data)})
                self.log(f'appunti copiati sui dispositivi (immagine, {len(data) // 1024} KB)', 'PC')

    async def _apply(self, fn, data, session, what):
        try:
            await asyncio.to_thread(fn, data)
        except Exception as e:
            self.log(f'Appunti non aggiornati: {e}', session.name)
            return
        self.ours.add(sequence())
        self.seq = sequence()
        self.log(f'appunti copiati sul PC ({what})', session.name)
        # anche gli altri dispositivi ricevono quello che ha copiato questo
        if isinstance(data, str):
            self._broadcast({'t': 'appunti', 'tipo': 'testo', 'testo': data}, skip=session.id)
        else:
            self.image, self.image_id = data, self.seq
            self._broadcast({'t': 'appunti', 'tipo': 'immagine', 'id': self.seq, 'dim': len(data)}, skip=session.id)

    def from_device_text(self, session, text):
        if not self.enabled or not isinstance(text, str) or not text or len(text) > MAX_TEXT:
            return
        asyncio.create_task(self._apply(write_text, text, session, f'testo, {len(text)} caratteri'))

    # ---- immagini via HTTPS (troppo grandi per il canale dei comandi) ----
    def _session(self, request):
        q = request.query
        dev = q.get('dev', '')
        if not self.app.devices.check(dev, q.get('token', '')):
            raise web.HTTPForbidden(text='dispositivo non abbinato')
        return self.app.session(dev)

    async def image_get(self, request):
        self._session(request)
        if not self.enabled or self.image is None:
            raise web.HTTPNotFound(text='nessuna immagine negli appunti')
        return web.Response(body=self.image, content_type='image/png', headers={'Cache-Control': 'no-store'})

    async def image_post(self, request):
        session = self._session(request)
        if not self.enabled:
            raise web.HTTPForbidden(text='appunti condivisi spenti sul PC')
        data = await request.content.read(MAX_IMAGE + 1)
        if not data or len(data) > MAX_IMAGE:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_IMAGE, actual_size=len(data))
        try:
            from PIL import Image
            Image.open(io.BytesIO(data)).verify()  # solo immagini vere
        except Exception:
            raise web.HTTPBadRequest(text='non è un\'immagine')
        await self._apply(write_image, data, session, f'immagine, {len(data) // 1024} KB')
        return web.json_response({'ok': True})

    def routes(self, router):
        router.add_get('/api/appunti/immagine', self.image_get)
        router.add_post('/api/appunti/immagine', self.image_post)
