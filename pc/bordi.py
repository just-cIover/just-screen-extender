"""Spinta del mouse contro il bordo, come il Controllo Universale del Mac.

Il PC non può sapere da solo dove si trova fisicamente l'iPad. Se però spingi il puntatore contro un
bordo del monitor dove non c'è nessuno schermo (es. a destra, perché l'iPad è lì), vuol dire che lo
schermo del dispositivo va da quella parte: il server lo sposta e il puntatore ci entra.

- Legge i movimenti del mouse con Raw Input: arrivano anche quando il puntatore è fermo contro il
  bordo, e misurano quanto stai spingendo.
- Conta solo mouse e touchpad veri. Si scartano: i movimenti in posizione assoluta (trascinamento dei
  file dai dispositivi, desktop remoto, tavolette), quelli che Windows ricava da tocchi e penna (hanno
  una firma) e quelli marcati da questo programma (SIGNATURE). Il dispositivo d'origine invece non
  serve: touchpad di precisione e molti mouse Bluetooth arrivano senza.
- Una spinta breve (arrivare al bordo per una barra di scorrimento, la barra delle applicazioni o la
  X di una finestra) non basta: serve una spinta decisa e continua, poi una pausa prima della prossima.
"""
import ctypes
import threading
import time
from ctypes import wintypes

import monitors

PUSH_COUNTS = 280    # movimento verso l'esterno (unità del mouse, ~pixel) da accumulare contro il bordo
PUSH_TIME = 0.4      # secondi di spinta continua, almeno
GAP = 0.25           # una pausa più lunga di così azzera la spinta
COOLDOWN = 4.0       # dopo uno spostamento, secondi prima di riconoscerne un altro
SIDES = ('destra', 'sinistra', 'sopra', 'sotto')
SIGNATURE = 0x53450001  # dwExtraInfo degli eventi del mouse simulati da Schermo Esteso (inject.py)
TOUCH_PEN = 0xFF515700  # firma di Windows sul mouse ricavato da tocchi e penna (MI_WP_SIGNATURE)

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_INPUT, WM_QUIT = 0x00FF, 0x0012
RID_INPUT, RIM_TYPEMOUSE, RIDEV_INPUTSINK = 0x10000003, 0, 0x00000100
MOUSE_MOVE_ABSOLUTE = 0x01
HWND_MESSAGE = wintypes.HWND(-3)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [('usUsagePage', wintypes.USHORT), ('usUsage', wintypes.USHORT),
                ('dwFlags', wintypes.DWORD), ('hwndTarget', wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [('dwType', wintypes.DWORD), ('dwSize', wintypes.DWORD),
                ('hDevice', wintypes.HANDLE), ('wParam', wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [('usFlags', wintypes.USHORT), ('ulButtons', wintypes.ULONG), ('ulRawButtons', wintypes.ULONG),
                ('lLastX', wintypes.LONG), ('lLastY', wintypes.LONG), ('ulExtraInformation', wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [('header', RAWINPUTHEADER), ('mouse', RAWMOUSE)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HANDLE),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HANDLE),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]


def _inside(m, x, y):
    return m['x'] <= x < m['x'] + m['w'] and m['y'] <= y < m['y'] + m['h']


class Pusher:
    """Riconosce la spinta contro un bordo libero. Separato dalla lettura del mouse, così si prova a parte.
    feed() riceve ogni movimento e restituisce (lato, monitor, (x, y)) quando la spinta è riconosciuta."""

    def __init__(self):
        self.side = None
        self.amount = 0
        self.start = self.last = 0.0
        self.quiet_until = 0.0

    def reset(self):
        self.side, self.amount = None, 0

    def feed(self, dx, dy, x, y, mons, now):
        if now < self.quiet_until:
            return None
        m = next((m for m in mons if _inside(m, x, y)), None)
        if not m or monitors.is_virtual(m):
            self.reset()  # solo dai bordi dei monitor veri del PC
            return None
        right, bottom = m['x'] + m['w'] - 1, m['y'] + m['h'] - 1
        side, push, nx, ny = None, 0, x, y
        if x >= right and dx > 0:
            side, push, nx = 'destra', dx, right + 1
        elif x <= m['x'] and dx < 0:
            side, push, nx = 'sinistra', -dx, m['x'] - 1
        elif y >= bottom and dy > 0:
            side, push, ny = 'sotto', dy, bottom + 1
        elif y <= m['y'] and dy < 0:
            side, push, ny = 'sopra', -dy, m['y'] - 1
        if not side or any(_inside(o, nx, ny) for o in mons):
            # non contro un bordo, o lì c'è già un altro schermo (il puntatore ci passa da solo)
            if not side and now - self.last > GAP:
                self.reset()
            return None
        if side != self.side or now - self.last > GAP:
            self.side, self.amount, self.start = side, 0, now
        self.amount += push
        self.last = now
        if self.amount >= PUSH_COUNTS and now - self.start >= PUSH_TIME:
            self.reset()
            self.quiet_until = now + COOLDOWN
            return side, m, (x, y)
        return None


class EdgeWatcher:
    """Legge il mouse in un thread suo (Raw Input su una finestra invisibile) e chiama on_push(lato, monitor, punto)."""

    def __init__(self, on_push, log=print):
        self.on_push = on_push
        self.log = log
        self.pusher = Pusher()
        self.thread_id = None
        self._mons, self._mons_at = [], 0.0

    def monitors(self, now):
        if now - self._mons_at > 1.0:  # la disposizione cambia di rado: la rileggo al massimo una volta al secondo
            self._mons, self._mons_at = monitors.list_monitors(), now
        return self._mons

    def _on_input(self, lparam):
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        buf = RAWINPUT()
        if user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf), ctypes.byref(size),
                                  ctypes.sizeof(RAWINPUTHEADER)) in (0, 0xFFFFFFFF):
            return
        if buf.header.dwType != RIM_TYPEMOUSE:
            return
        if buf.mouse.usFlags & MOUSE_MOVE_ABSOLUTE:
            return  # posizione assoluta (trascinamento dai dispositivi, desktop remoto, tavolette): non una spinta
        extra = buf.mouse.ulExtraInformation
        if extra == SIGNATURE or extra & 0xFFFFFF00 == TOUCH_PEN:
            return  # simulato da questo programma, o ricavato da tocchi e penna
        dx, dy = buf.mouse.lLastX, buf.mouse.lLastY
        if not dx and not dy:
            return
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        now = time.monotonic()
        hit = self.pusher.feed(dx, dy, pt.x, pt.y, self.monitors(now), now)
        if hit:
            self.on_push(*hit)

    def run(self, stop=None):
        self.thread_id = kernel32.GetCurrentThreadId()

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                try:
                    self._on_input(lparam)
                except Exception as e:  # un errore qui non deve fermare la lettura del mouse
                    self.log(f'Spinta del mouse: errore {e!r}')
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)  # va tenuto in vita
        inst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst, lpszClassName='SchermoEstesoBordi')
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, 'Schermo Esteso bordi', 0, 0, 0, 0, 0,
                                      HWND_MESSAGE, None, inst, None)
        rid = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, hwnd)  # pagina 1, uso 2 = mouse (anche i touchpad)
        if not hwnd or not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
            self.log(f'Spinta del mouse non disponibile (errore {ctypes.get_last_error()})')
            return
        if stop is not None:
            threading.Thread(target=lambda: (stop.wait(), self.quit()), daemon=True).start()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.DestroyWindow(hwnd)

    def quit(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


def place(side, mon, pt, size, others):
    """Dove mettere uno schermo grande size=(w, h) sul lato indicato del monitor mon, all'altezza (o alla
    larghezza) del punto in cui hai spinto, senza sovrapporsi agli altri schermi.
    Restituisce (x, y), oppure None se su quel lato non c'è posto."""
    w, h = size
    px, py = pt
    if side in ('destra', 'sinistra'):
        x = mon['x'] + mon['w'] if side == 'destra' else mon['x'] - w
        lo, hi = mon['y'] - h + 1, mon['y'] + mon['h'] - 1  # deve toccare il monitor almeno per un pixel
        y = max(lo, min(hi, py - h // 2))
        cand = [(x, y)] + [(x, v) for v in range(lo, hi + 1, 50)]
        cand.sort(key=lambda c: abs(c[1] - y))
    else:
        y = mon['y'] - h if side == 'sopra' else mon['y'] + mon['h']
        lo, hi = mon['x'] - w + 1, mon['x'] + mon['w'] - 1
        x = max(lo, min(hi, px - w // 2))
        cand = [(x, y)] + [(v, y) for v in range(lo, hi + 1, 50)]
        cand.sort(key=lambda c: abs(c[0] - x))
    for cx, cy in cand:
        if not any(cx < o['x'] + o['w'] and o['x'] < cx + w and cy < o['y'] + o['h'] and o['y'] < cy + h
                   for o in others):
            return cx, cy
    return None
