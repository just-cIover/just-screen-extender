"""Mouse push against the edge, like Universal Control on a Mac.

The PC can't know by itself where the iPad physically is. But if you push the pointer against an
edge of the monitor where there's no screen (e.g. on the right, because the iPad is there), it means
the device screen goes on that side: the server moves it and the pointer goes in.

- Reads mouse movement with Raw Input: it arrives even when the pointer is stopped against the
  edge, and measures how hard you're pushing.
- Only real mice and touchpads count. Discarded: movements in absolute position (file drags
  from the devices, remote desktop, tablets), the ones Windows derives from touch and pen (they have
  a signature) and the ones marked by this program (SIGNATURE). The source device isn't needed:
  precision touchpads and many Bluetooth mice arrive without one.
- A short push (reaching the edge for a scroll bar, the taskbar or a window's X) isn't enough:
  it takes a firm, continuous push, then a pause before the next one.
"""
import ctypes
import threading
import time
from ctypes import wintypes

import monitors

PUSH_COUNTS = 280    # outward movement (mouse units, ~pixels) to accumulate against the edge
PUSH_TIME = 0.4      # seconds of continuous push, at least
GAP = 0.25           # a pause longer than this resets the push
COOLDOWN = 4.0       # after a move, seconds before recognizing another one
SIDES = ('destra', 'sinistra', 'sopra', 'sotto')
SIGNATURE = 0x53450001  # dwExtraInfo of the mouse events simulated by Schermo Esteso (inject.py)
TOUCH_PEN = 0xFF515700  # Windows signature on mouse input derived from touch and pen (MI_WP_SIGNATURE)

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
    """Recognizes a push against a free edge. Separate from reading the mouse, so it can be tested on its own.
    feed() gets every movement and returns (side, monitor, (x, y)) when the push is recognized."""

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
            self.reset()  # only from the edges of the PC's real monitors
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
            # not against an edge, or there's already another screen there (the pointer goes through by itself)
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
    """Reads the mouse in its own thread (Raw Input on an invisible window) and calls on_push(side, monitor, point)."""

    def __init__(self, on_push, log=print):
        self.on_push = on_push
        self.log = log
        self.pusher = Pusher()
        self.thread_id = None
        self._mons, self._mons_at = [], 0.0

    def monitors(self, now):
        if now - self._mons_at > 1.0:  # the layout rarely changes: I read it again at most once a second
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
            return  # absolute position (drag from the devices, remote desktop, tablets): not a push
        extra = buf.mouse.ulExtraInformation
        if extra == SIGNATURE or extra & 0xFFFFFF00 == TOUCH_PEN:
            return  # simulated by this program, or derived from touch and pen
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
                except Exception as e:  # an error here must not stop reading the mouse
                    self.log(f'Spinta del mouse: errore {e!r}')
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)  # must be kept alive
        inst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst, lpszClassName='SchermoEstesoBordi')
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, 'Schermo Esteso bordi', 0, 0, 0, 0, 0,
                                      HWND_MESSAGE, None, inst, None)
        rid = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, hwnd)  # page 1, usage 2 = mouse (touchpads too)
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
    """Where to put a screen of size=(w, h) on the given side of monitor mon, at the height (or
    width) of the point where you pushed, without overlapping the other screens.
    Returns (x, y), or None if there's no room on that side."""
    w, h = size
    px, py = pt
    if side in ('destra', 'sinistra'):
        x = mon['x'] + mon['w'] if side == 'destra' else mon['x'] - w
        lo, hi = mon['y'] - h + 1, mon['y'] + mon['h'] - 1  # it must touch the monitor by at least one pixel
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
