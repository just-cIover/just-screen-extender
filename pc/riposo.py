"""Sleep: when the PC sleeps, Schermo Esteso stops too.

With Windows modern standby (low-power S0) programs keep running in bursts even
with the PC "off". The server, checking screens, clipboard and files several times a second,
drained its battery for nothing, and the devices stayed on waiting for video.

- Windows notifies this module when the PC screen turns off or on (lid closed,
  power button, standby) and when the PC goes to sleep or wakes up.
- While the PC sleeps, the server's periodic checks use nap(): they stay still without waking
  the processor and resume as soon as the PC wakes up.
- The screen turning off for inactivity doesn't count: with a device connected the server
  keeps the PC screen on (keep_pc_awake), so the screen turns off only if you decide so.
"""
import asyncio
import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_POWERBROADCAST, WM_QUIT = 0x0218, 0x0012
PBT_APMSUSPEND, PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC, PBT_POWERSETTINGCHANGE = 0x4, 0x7, 0x12, 0x8013


class GUID(ctypes.Structure):
    _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16), ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]

    def __eq__(self, o):
        return bytes(self) == bytes(o)


# GUID_CONSOLE_DISPLAY_STATE: PC screen off (0), on (1), dimmed (2)
DISPLAY_STATE = GUID(0x6FE69556, 0x704A, 0x47A0, (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47))


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [('PowerSetting', GUID), ('DataLength', wintypes.DWORD), ('Data', wintypes.DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HANDLE),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HANDLE),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]

_awake = None        # asyncio.Event: set when the PC is awake
_woke_at = 0.0       # time.monotonic() of the last wake-up


def asleep():
    return _awake is not None and not _awake.is_set()


def woke_since(seconds):
    """The PC woke up in the last `seconds` seconds (connections are still being re-established)."""
    return time.monotonic() - _woke_at < seconds


async def nap(seconds):
    """Like asyncio.sleep, but while the PC sleeps it stays still until it wakes up."""
    await asyncio.sleep(seconds)
    if _awake is not None and not _awake.is_set():
        await _awake.wait()


async def wait_awake():
    if _awake is not None:
        await _awake.wait()


class Watcher:
    """Receives the Windows notifications in its own thread (invisible window) and calls on_change(asleep)
    in the server's asyncio loop, only when the state really changes."""

    def __init__(self, loop, on_change, log=print):
        global _awake
        self.loop = loop
        self.on_change = on_change
        self.log = log
        self.thread_id = None
        self.display_off = False
        self.suspended = False
        _awake = asyncio.Event()
        _awake.set()

    def _update(self):
        sleeping = self.display_off or self.suspended
        self.loop.call_soon_threadsafe(self._apply, sleeping)

    def _apply(self, sleeping):
        global _woke_at
        if sleeping == (not _awake.is_set()):
            return
        if sleeping:
            _awake.clear()
        else:
            _woke_at = time.monotonic()
            _awake.set()
        try:
            self.on_change(sleeping)
        except Exception as e:
            self.log(f'Riposo: errore {e!r}')

    def run(self, stop=None):
        self.thread_id = kernel32.GetCurrentThreadId()

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_POWERBROADCAST:
                if wparam == PBT_APMSUSPEND:
                    self.suspended = True
                    self._update()
                elif wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                    self.suspended = False
                    self._update()
                elif wparam == PBT_POWERSETTINGCHANGE and lparam:
                    s = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
                    if s.PowerSetting == DISPLAY_STATE:
                        self.display_off = s.Data == 0
                        self._update()
                return 1
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)  # must be kept alive
        inst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst, lpszClassName='SchermoEstesoRiposo')
        user32.RegisterClassW(ctypes.byref(cls))
        # a normal window that is never shown: "message-only" windows don't receive sleep notifications
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, 'Schermo Esteso riposo', 0, 0, 0, 0, 0,
                                      None, None, inst, None)
        if not hwnd or not user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(DISPLAY_STATE), 0):
            self.log(f'Riposo non disponibile (errore {ctypes.get_last_error()}): il programma resta sempre attivo')
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
