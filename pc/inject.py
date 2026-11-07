"""Touch and mouse from the iPad to Windows.

- "Touch" mode: real Windows multi-finger touch (InjectTouchInput), so
  scrolling, zoom and long press work as on a touch screen.
- "Mouse" mode: cursor, left/right click and wheel (SendInput).
Coordinates arrive normalized (0..1) and are mapped onto the iPad's monitor.
"""
import ctypes
from ctypes import wintypes

import time

import finestre
import monitors  # also sets DPI awareness

user32 = ctypes.windll.user32
# copy with use_last_error, to read the real InjectTouchInput error code
_InjectTouchInput = ctypes.WinDLL('user32', use_last_error=True).InjectTouchInput

# --- touch -----------------------------------------------------------------
PT_TOUCH = 2
F_NEW, F_INRANGE, F_INCONTACT = 0x1, 0x2, 0x4
F_DOWN, F_UPDATE, F_UP = 0x10000, 0x20000, 0x40000
MASK_ALL = 0x1 | 0x2 | 0x4  # contact area, orientation, pressure
MAX_CONTACTS = 10


class POINTER_INFO(ctypes.Structure):
    _fields_ = [('pointerType', ctypes.c_uint32), ('pointerId', ctypes.c_uint32), ('frameId', ctypes.c_uint32),
                ('pointerFlags', ctypes.c_uint32), ('sourceDevice', ctypes.c_void_p), ('hwndTarget', ctypes.c_void_p),
                ('ptPixelLocation', wintypes.POINT), ('ptHimetricLocation', wintypes.POINT),
                ('ptPixelLocationRaw', wintypes.POINT), ('ptHimetricLocationRaw', wintypes.POINT),
                ('dwTime', ctypes.c_uint32), ('historyCount', ctypes.c_uint32), ('InputData', ctypes.c_int32),
                ('dwKeyStates', ctypes.c_uint32), ('PerformanceCount', ctypes.c_uint64),
                ('ButtonChangeType', ctypes.c_int)]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [('pointerInfo', POINTER_INFO), ('touchFlags', ctypes.c_uint32), ('touchMask', ctypes.c_uint32),
                ('rcContact', wintypes.RECT), ('rcContactRaw', wintypes.RECT),
                ('orientation', ctypes.c_uint32), ('pressure', ctypes.c_uint32)]


# --- mouse -----------------------------------------------------------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', _U)]


M_LEFTDOWN, M_LEFTUP, M_RIGHTDOWN, M_RIGHTUP = 0x2, 0x4, 0x8, 0x10
M_WHEEL, M_HWHEEL = 0x800, 0x1000
BUTTONS = {0: (M_LEFTDOWN, M_LEFTUP), 2: (M_RIGHTDOWN, M_RIGHTUP)}


def _mouse(flags, data=0):
    i = INPUT(type=0)
    # dwExtraInfo: identification mark, so the mouse push (bordi.py) ignores these events
    i.u.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(data).value, flags, 0, 0x53450001)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def _px(rect, nx, ny):
    x, y, w, h = rect
    nx = min(max(nx, 0.0), 1.0)
    ny = min(max(ny, 0.0), 1.0)
    return x + round(nx * (w - 1)), y + round(ny * (h - 1))


class Pad:
    """Input from one device: maps the coordinates onto its screen and passes everything to the shared
    Injector (Windows accepts a single set of touches for the whole PC)."""

    def __init__(self, hub):
        self.hub = hub
        self.rect = None
        self.pen_as_finger = False
        self.last_touch = 0.0  # last touch sent to Windows (time.monotonic)
        self.pen_app = ''  # program under the tip at the last touch-down

    def point(self, nx, ny):
        """Screen coordinates (pixels) of a normalized point, or None if the screen is off."""
        return _px(self.rect, nx, ny) if self.rect else None

    def set_monitor(self, m):
        self.rect = (m['x'], m['y'], m['w'], m['h']) if m else None

    def touch(self, ev, pid, nx, ny):
        self.last_touch = time.monotonic()
        if self.rect:
            self.hub.touch(ev, (id(self), pid), *_px(self.rect, nx, ny))

    def mouse(self, ev, button, nx, ny):
        if self.rect:
            self.hub.mouse(ev, button, *_px(self.rect, nx, ny))

    def wheel(self, nx, ny, dx, dy):
        if self.rect:
            self.hub.wheel(*_px(self.rect, nx, ny), dx, dy)

    def pen(self, ev, nx, ny, pressure, tx, ty, eraser, auto=False):
        """Returns, when the tip touches down, 'penna' or 'dito': how it's used in this stroke."""
        if not self.rect:
            return None
        x, y = _px(self.rect, nx, ny)
        if ev == 'd':
            # automatic: writes only in drawing programs, acts as a finger elsewhere (scrolls and taps)
            ink, self.pen_app = finestre.ink_app_at(x, y) if auto else (True, '')
            self.pen_as_finger = not ink
        if self.pen_as_finger:
            self.last_touch = time.monotonic()
            self.hub.touch(ev, (id(self), 'penna'), x, y)
            return 'dito' if ev == 'd' else None
        if not self.hub.pen.ok:  # no Windows pen: act as a mouse
            self.hub.mouse(ev, 0, x, y)
        else:
            self.hub.pen.event(id(self), ev, x, y, pressure, tx, ty, eraser)
        return 'penna' if ev == 'd' else None

    def release_all(self):
        self.hub.release_all(id(self))
        self.hub.pen.lift(id(self))


class Injector:
    def __init__(self, log):
        self.log = log
        self.contacts = {}  # (device, touch id) -> [Windows id, x, y]
        self.touch_ok = bool(user32.InitializeTouchInjection(MAX_CONTACTS, 1))  # 1 = standard visual feedback
        if not self.touch_ok:
            log('Tocco nativo non disponibile su questo PC: uso il mouse')
        self.pen = Pen(log)

    # ---- touch ----
    def touch(self, ev, pid, x, y):
        if not self.touch_ok:
            return self.mouse(ev, 0, x, y)
        if ev == 'd':
            if pid in self.contacts or len(self.contacts) >= MAX_CONTACTS:
                return
            used = {c[0] for c in self.contacts.values()}
            wid = next(i for i in range(MAX_CONTACTS) if i not in used)
            self.contacts[pid] = [wid, x, y]
            self._inject({pid: F_DOWN | F_INRANGE | F_INCONTACT})
        elif ev == 'm':
            c = self.contacts.get(pid)
            if c:
                c[1], c[2] = x, y
                self._inject({})
        else:  # 'u' lifted or 'c' cancelled
            c = self.contacts.get(pid)
            if not c:
                return
            if (c[1], c[2]) != (x, y):
                # Windows refuses (error 87) a release at a point different from the last move:
                # first move the contact, then release it there
                c[1], c[2] = x, y
                self._inject({})
            if pid in self.contacts:
                self._inject({pid: F_UP})
                self.contacts.pop(pid, None)

    def keepalive(self):
        """Windows cancels contacts that sit still if it gets no updates: I repeat them.
        Returns True if something is touching (finger or pen)."""
        if self.contacts:
            self._inject({})
        self.pen.keepalive()
        return bool(self.contacts) or self.pen.down

    def release_all(self, owner=None):
        mine = [k for k in self.contacts if owner is None or k[0] == owner]
        if mine:
            self._inject({k: F_UP for k in mine})
            for k in mine:
                self.contacts.pop(k, None)

    def _inject(self, special):
        n = len(self.contacts)
        arr = (POINTER_TOUCH_INFO * n)()
        for k, (pid, (wid, x, y)) in enumerate(self.contacts.items()):
            t = arr[k]
            t.pointerInfo.pointerType = PT_TOUCH
            t.pointerInfo.pointerId = wid
            t.pointerInfo.pointerFlags = special.get(pid, F_UPDATE | F_INRANGE | F_INCONTACT)
            t.pointerInfo.ptPixelLocation = wintypes.POINT(x, y)
            t.touchMask = MASK_ALL
            t.rcContact = wintypes.RECT(x - 2, y - 2, x + 2, y + 2)
            t.orientation = 90
            t.pressure = 32000
        if not _InjectTouchInput(n, arr):
            err = ctypes.get_last_error()
            self.log(f'Tocco rifiutato da Windows (errore {err}): azzero i tocchi')
            # release the contacts one by one at their last known position, so Windows doesn't keep them "pressed"
            for k in range(n):
                one = (POINTER_TOUCH_INFO * 1)(arr[k])
                one[0].pointerInfo.pointerFlags = F_UP
                _InjectTouchInput(1, one)
            self.contacts.clear()

    # ---- mouse ----
    def mouse(self, ev, button, x, y):
        user32.SetCursorPos(x, y)
        down, up = BUTTONS.get(button, BUTTONS[0])
        if ev == 'd':
            _mouse(down)
        elif ev in ('u', 'c'):
            _mouse(up)
        elif ev == 'click':
            _mouse(down)
            _mouse(up)

    def wheel(self, x, y, dx, dy):
        user32.SetCursorPos(x, y)
        if dy:
            _mouse(M_WHEEL, int(-dy))
        if dx:
            _mouse(M_HWHEEL, int(dx))


# --- keyboard: text typed on the iPad (passwords from the Keychain too) ------------------
KEY_UP, KEY_UNICODE = 0x2, 0x4
KEYS = {'invio': 0x0D, 'tab': 0x09, 'canc': 0x08, 'esc': 0x1B}
MAX_TYPE = 10_000


def _send_keys(events):
    """events = [(virtual key, UTF-16 character, flags)], sent in batches."""
    for i in range(0, len(events), 64):
        block = events[i:i + 64]
        arr = (INPUT * len(block))()
        for k, (vk, scan, flags) in enumerate(block):
            arr[k].type = 1  # INPUT_KEYBOARD
            arr[k].u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        user32.SendInput(len(block), arr, ctypes.sizeof(INPUT))


def type_text(text):
    """Types the text into the selected field on the PC, character by character (any language or emoji)."""
    events = []
    data = text[:MAX_TYPE].replace('\r\n', '\n').replace('\r', '\n').encode('utf-16-le')
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i:i + 2], 'little')
        if unit == 10:  # newline = Enter
            events += [(KEYS['invio'], 0, 0), (KEYS['invio'], 0, KEY_UP)]
        else:
            events += [(0, unit, KEY_UNICODE), (0, unit, KEY_UNICODE | KEY_UP)]
    _send_keys(events)


COMBOS = {'copia': ord('C'), 'taglia': ord('X'), 'incolla': ord('V'), 'tutto': ord('A')}  # with Ctrl
VK_CONTROL = 0x11


def press(name):
    vk = KEYS.get(name)
    if vk:
        _send_keys([(vk, 0, 0), (vk, 0, KEY_UP)])
    elif name in COMBOS:
        k = COMBOS[name]
        _send_keys([(VK_CONTROL, 0, 0), (k, 0, 0), (k, 0, KEY_UP), (VK_CONTROL, 0, KEY_UP)])


# --- pen (Apple Pencil -> Windows pen, with pressure and tilt) -----------
# Windows 10 1809+ allows creating a "synthetic" pen: programs see it as a
# real pen (Windows Ink), so pressure in drawing programs, signatures in Edge PDFs,
# handwriting in text boxes, eraser.
PT_PEN = 3
PEN_FLAG_INVERTED, PEN_FLAG_ERASER = 0x2, 0x4
PEN_MASK_PRESSURE, PEN_MASK_TILT_X, PEN_MASK_TILT_Y = 0x1, 0x4, 0x8
FEEDBACK_DEFAULT = 1


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [('pointerInfo', POINTER_INFO), ('penFlags', ctypes.c_uint32), ('penMask', ctypes.c_uint32),
                ('pressure', ctypes.c_uint32), ('rotation', ctypes.c_uint32),
                ('tiltX', ctypes.c_int32), ('tiltY', ctypes.c_int32)]


class _PTI_U(ctypes.Union):
    _fields_ = [('touchInfo', POINTER_TOUCH_INFO), ('penInfo', POINTER_PEN_INFO)]


class POINTER_TYPE_INFO(ctypes.Structure):
    _fields_ = [('type', ctypes.c_uint32), ('u', _PTI_U)]


_u32e = ctypes.WinDLL('user32', use_last_error=True)
try:
    _CreatePen = _u32e.CreateSyntheticPointerDevice
    _CreatePen.restype = ctypes.c_void_p
    _CreatePen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    _InjectPen = _u32e.InjectSyntheticPointerInput
    _InjectPen.restype = wintypes.BOOL
    _InjectPen.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINTER_TYPE_INFO), ctypes.c_uint32]
except AttributeError:  # Windows too old
    _CreatePen = _InjectPen = None


class Pen:
    """A Windows pen. States: out of range -> in contact -> out of range."""

    def __init__(self, log):
        self.log = log
        self.dev = _CreatePen(PT_PEN, 1, FEEDBACK_DEFAULT) if _CreatePen else None
        self.ok = bool(self.dev)
        if not self.ok:
            log('Penna di Windows non disponibile su questo PC: la Pencil farà da mouse')
        self.down = False
        self.last = None  # (x, y, pressure, tiltX, tiltY, eraser) of the last point
        self.owner = None

    def _send(self, flags, x, y, pressure, tx, ty, eraser):
        info = POINTER_TYPE_INFO(type=PT_PEN)
        p = info.u.penInfo
        p.pointerInfo.pointerType = PT_PEN
        p.pointerInfo.pointerFlags = flags
        # the simulated pen wants coordinates from the top-left corner of the whole desktop
        # (with a screen to the left of or above the main one, that corner has negative coordinates)
        vx, vy, _, _ = monitors.virtual_screen()
        p.pointerInfo.ptPixelLocation = wintypes.POINT(x - vx, y - vy)
        p.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
        p.pressure = max(0, min(1024, int(pressure * 1024)))
        p.tiltX, p.tiltY = max(-90, min(90, int(tx))), max(-90, min(90, int(ty)))
        if eraser:
            p.penFlags = PEN_FLAG_INVERTED | (PEN_FLAG_ERASER if flags & F_INCONTACT else 0)
        if not _InjectPen(self.dev, ctypes.byref(info), 1):
            return ctypes.get_last_error()
        return 0

    def event(self, owner, ev, x, y, pressure=0.5, tx=0, ty=0, eraser=False):
        if not self.ok:
            return
        if ev == 'd':
            if self.down:  # contact left open (message lost): close it first
                self.lift()
            self._send(F_INRANGE | F_UPDATE, x, y, 0, tx, ty, eraser)  # comes into range above the point
            err = self._send(F_DOWN | F_INRANGE | F_INCONTACT, x, y, pressure, tx, ty, eraser)
            if err:
                self.log(f'Penna rifiutata da Windows (errore {err})')
                return
            self.down, self.owner = True, owner
        elif ev == 'm' and self.down and owner == self.owner:
            self._send(F_UPDATE | F_INRANGE | F_INCONTACT, x, y, pressure, tx, ty, eraser)
        elif ev in ('u', 'c') and self.down and owner == self.owner:
            self.last = (x, y, pressure, tx, ty, eraser)
            self.lift()
            return
        else:
            return
        self.last = (x, y, pressure, tx, ty, eraser)

    def lift(self, owner=None):
        if not self.down or (owner is not None and owner != self.owner):
            return
        x, y, _, tx, ty, er = self.last
        self._send(F_UP | F_INRANGE, x, y, 0, tx, ty, er)
        self._send(F_UPDATE, x, y, 0, tx, ty, er)  # leaves range
        self.down = False

    def keepalive(self):
        """As with touch: with the pen still I repeat the last point, so Windows doesn't cancel it."""
        if self.down and self.last:
            x, y, p, tx, ty, er = self.last
            self._send(F_UPDATE | F_INRANGE | F_INCONTACT, x, y, p, tx, ty, er)
