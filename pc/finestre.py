"""Moves the windows of a screen that is turning off to the main monitor, in the background.

The windows are put below all the others and don't take focus, so they don't cover
what's already on the main monitor. Position and size are scaled
proportionally into the work area of the main monitor.
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

import monitors  # also sets DPI awareness

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 2, 3, 4, 7
HWND_BOTTOM = ctypes.c_void_p(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
WS_EX_TOOLWINDOW = 0x80
GWL_EXSTYLE = -20
DWMWA_CLOAKED = 14
MONITOR_DEFAULTTONEAREST = 2

# system windows not to touch (desktop, taskbars)
SKIP_CLASSES = {'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'Windows.UI.Core.CoreWindow'}

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.MonitorFromRect.restype = wintypes.HMONITOR
user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT),
                ('rcNormalPosition', wintypes.RECT)]


def _monitor_device(rect):
    hmon = user32.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)
    mi = monitors.MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.szDevice


def _primary_work():
    mi = monitors.MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(mi)
    hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # 1 = main monitor
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r, w = mi.rcMonitor, mi.rcWork
    return (w.left, w.top, w.right - w.left, w.bottom - w.top), (w.left - r.left, w.top - r.top)


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def windows_on(device):
    """Normal windows (visible, minimized too) that are on the given screen,
    from top to bottom."""
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or _class(hwnd) in SKIP_CLASSES:
            return True
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True  # windows hidden by modern apps
        wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return True
        if wp.showCmd == SW_SHOWMINIMIZED:
            rc = wp.rcNormalPosition  # minimized: what counts is where it would come back
        else:
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
        if _monitor_device(rc) == device:
            found.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def move_to_primary(device, log=print):
    """Brings the windows of the given screen to the main monitor, below all the others."""
    src = monitors.by_device(device)
    if not src or src['primary']:
        return 0
    (wx, wy, ww, wh), (offx, offy) = _primary_work()
    fg = user32.GetForegroundWindow()
    moved = 0
    for hwnd in windows_on(device):
        wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            continue
        rc = wp.rcNormalPosition
        # rcNormalPosition is in work-area coordinates: convert it to screen coordinates
        w = min(rc.right - rc.left, ww)
        h = min(rc.bottom - rc.top, wh)
        fx = (rc.left + offx - src['x']) / max(src['w'] - w, 1)
        fy = (rc.top + offy - src['y']) / max(src['h'] - h, 1)
        nx = wx + round(min(max(fx, 0), 1) * (ww - w))
        ny = wy + round(min(max(fy, 0), 1) * (wh - h))
        wp.rcNormalPosition = wintypes.RECT(nx - offx, ny - offy, nx - offx + w, ny - offy + h)
        if wp.showCmd == SW_SHOWMINIMIZED:
            wp.showCmd = SW_SHOWMINNOACTIVE
        elif wp.showCmd != SW_SHOWMAXIMIZED:
            wp.showCmd = SW_SHOWNOACTIVATE
        wp.flags = 0
        if user32.SetWindowPlacement(hwnd, ctypes.byref(wp)):
            # at the bottom of the stack: top to bottom, so their order among themselves stays the same
            user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            moved += 1
        else:
            log(f'Finestra "{_title(hwnd)}" non spostata (forse è di un programma avviato come amministratore)')
    if fg and user32.GetForegroundWindow() != fg:
        user32.SetForegroundWindow(fg)
    return moved



# --- opening a file directly on a device screen -----------------------
BROWSERS = {'msedge.exe': '--new-window', 'chrome.exe': '--new-window', 'brave.exe': '--new-window',
            'firefox.exe': '-new-window'}
SW_MAXIMIZE = 3


def _all_windows():
    """Normal visible windows, on any screen."""
    found = set()
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and _class(hwnd) not in SKIP_CLASSES
                and not user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW and _title(hwnd)):
            cloaked = wintypes.DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if not cloaked.value:
                found.add(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def _every_window():
    """All the main windows that exist, hidden or on other desktops too."""
    found = set()
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proc(lambda h, _: found.add(h) or True), 0)
    return found


def _associated_exe(path):
    """Program Windows uses to open this type of file (e.g. msedge.exe for PDFs)."""
    ext = os.path.splitext(path)[1]
    if not ext:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD(len(buf))
    # ASSOCF_NOTRUNCATE=0x20, ASSOCSTR_EXECUTABLE=2
    if ctypes.windll.shlwapi.AssocQueryStringW(0x20, 2, ext, None, buf, ctypes.byref(n)) != 0:
        return None
    return buf.value


def open_on(path, monitor, log=print, wait=12, edge_profile=None):
    """Opens the file with its program and brings the new window, maximized, to the given screen.
    monitor = {'x', 'y', 'w', 'h'}. Returns True if the window was moved.
    Only a window that didn't exist before is moved: never a window the user already had open."""
    before = _every_window()
    exe = _associated_exe(path)
    name = os.path.basename(exe or '').lower()
    if name == 'msedge.exe' and edge_profile:
        # separate app profile (no account): the file doesn't end up among your Edge tabs
        subprocess.Popen([exe, '--user-data-dir=' + edge_profile, '--new-window', '--no-first-run',
                          '--no-default-browser-check', '--disable-sync',
                          '--disable-features=msImplicitSignin,msEdgeImplicitSignin,msAutoSignIn', path])
    elif name in BROWSERS:  # other browsers: at least a new window instead of a tab
        subprocess.Popen([exe, BROWSERS[name], path])
    else:
        os.startfile(path)
    deadline = time.monotonic() + wait
    hwnd = None
    while time.monotonic() < deadline and not hwnd:
        time.sleep(0.25)
        new = _all_windows() - before
        hwnd = next(iter(new), None)
    if not hwnd:
        log(f'{os.path.basename(path)} aperto sul PC, ma non ho trovato una finestra nuova da spostare '
            '(il programma l\'ha aperto in una finestra già aperta)')
        return False
    time.sleep(0.4)  # let the program finish its initial positioning
    (_, _, _, _), (offx, offy) = _primary_work()
    x, y, w, h = monitor['x'], monitor['y'], monitor['w'], monitor['h']
    wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    m = 40  # "normal" position (when the window isn't maximized): almost the whole screen
    wp.rcNormalPosition = wintypes.RECT(x + m - offx, y + m - offy, x + w - m - offx, y + h - m - offy)
    wp.showCmd = SW_SHOWNOACTIVATE
    wp.flags = 0
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)
    return True


# --- program under a point --------------------------------------------------------
kernel32 = ctypes.windll.kernel32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HMONITOR
kernel32.OpenProcess.restype = wintypes.HANDLE

# programs where the Pencil must write (in all the others it acts as a finger: scrolls and taps)
INK_APPS = {'mspaint.exe', 'onenote.exe', 'onenoteim.exe', 'whiteboard.exe', 'winword.exe', 'powerpnt.exe',
            'acrobat.exe', 'acrord32.exe', 'krita.exe', 'photoshop.exe', 'inkscape.exe', 'clipstudiopaint.exe',
            'xournalpp.exe', 'journal.exe', 'sketchbook.exe', 'drawboardpdf.exe', 'snippingtool.exe',
            'screensketch.exe', 'freshpaint.exe', 'paintdotnet.exe', 'gimp.exe', 'mypaint.exe', 'blender.exe',
            'zoomit.exe', 'zoomit64.exe', 'goodnotes.exe', 'notability.exe'}
BROWSER_EXES = {'msedge.exe', 'chrome.exe', 'brave.exe', 'firefox.exe', 'opera.exe', 'vivaldi.exe'}
# other drawing programs: one per line in programmi-penna.txt (e.g. "notepad3.exe"), without touching the code
INK_APPS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'programmi-penna.txt')


def panel_window_open(title='Schermo Esteso'):
    """Does the control window (Edge in app mode, title "Schermo Esteso") still exist, even minimized?"""
    return any(_title(h) == title and process_name(h) == 'msedge.exe' for h in _every_window())


def process_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ''
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)


_extra = (0.0, set())


def _ink_apps():
    global _extra
    try:
        t = os.path.getmtime(INK_APPS_FILE)
    except OSError:
        return INK_APPS
    if t != _extra[0]:
        with open(INK_APPS_FILE, encoding='utf-8') as f:
            names = {l.strip().lower() for l in f if l.strip() and not l.startswith('#')}
        _extra = (t, {n if n.endswith('.exe') else n + '.exe' for n in names})
    return INK_APPS | _extra[1]


def ink_app_at(x, y):
    """(yes/no, program): whether there's a drawing/writing program at the point (or a PDF in the browser)."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return False, ''
    root = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    child, top = process_name(hwnd), process_name(root)  # modern apps: the child window belongs to the real program
    name = child or top
    if {child, top} & _ink_apps():
        return True, name
    return bool({child, top} & BROWSER_EXES) and '.pdf' in _title(root).lower(), name


def browser_at(x, y):
    """True if there's a Chromium/Edge/Electron or Firefox page at the point: there a double tap doesn't
    select the word, it takes a mouse double click."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, 2) or hwnd
    classes = (_class(hwnd), _class(root))
    return any(c.startswith(('Chrome_', 'Mozilla')) for c in classes)


# --- apps opened from Start on the screen where you searched for them ----------------------------
START_HOSTS = {'startmenuexperiencehost.exe', 'searchhost.exe', 'searchapp.exe'}
START_WINDOW = 20  # seconds after Start during which a new window counts as "opened from there"


def _monitor_rects(hmon):
    mi = monitors.MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.szDevice, mi.rcMonitor, mi.rcWork


def move_to_monitor(hwnd, hmon):
    """Moves a window to the given screen keeping its size and position proportional."""
    wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return False
    rc = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    _, src, _ = _monitor_rects(user32.MonitorFromRect(ctypes.byref(rc), MONITOR_DEFAULTTONEAREST))
    _, _, dst = _monitor_rects(hmon)
    (_, _, _, _), (offx, offy) = _primary_work()
    n = wp.rcNormalPosition
    ww, wh = dst.right - dst.left, dst.bottom - dst.top
    w, h = min(n.right - n.left, ww), min(n.bottom - n.top, wh)
    fx = (n.left + offx - src.left) / max(src.right - src.left - w, 1)
    fy = (n.top + offy - src.top) / max(src.bottom - src.top - h, 1)
    nx = dst.left + round(min(max(fx, 0), 1) * (ww - w))
    ny = dst.top + round(min(max(fy, 0), 1) * (wh - h))
    maximized = wp.showCmd == SW_SHOWMAXIMIZED
    if maximized:  # going through "normal" really moves maximized windows too
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    wp.rcNormalPosition = wintypes.RECT(nx - offx, ny - offy, nx - offx + w, ny - offy + h)
    wp.showCmd = 1  # SW_SHOWNORMAL
    wp.flags = 0
    ok = user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
    if maximized:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return bool(ok)


WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_SHOW = 0x0003, 0x8002
WS_CAPTION = 0x00C00000


def _app_window(hwnd):
    """Main window of a program (not menus, tooltips, inner windows)."""
    if user32.GetAncestor(hwnd, 2) != hwnd or not user32.IsWindowVisible(hwnd):
        return False
    if _class(hwnd) in SKIP_CLASSES or user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    return bool(user32.GetWindowLongPtrW(hwnd, -16) & WS_CAPTION)  # GWL_STYLE


class StartFollower:
    """Remembers which screen you opened Start on; new windows that appear right after on
    another screen are brought there. Windows notifies as soon as a window appears (no waiting);
    for 2 s I check it again, because some programs move back to where they were last time."""

    def __init__(self, log=print):
        self.log = log
        self.start_mon = None
        self.start_at = 0.0
        self.watch = {}  # moved window -> until when to check it again

    def _on_event(self, hook, event, hwnd, obj, child, thread, ms):
        try:
            if obj != 0 or child != 0 or not hwnd:  # only events of the whole window
                return
            now = time.monotonic()
            if event == EVENT_SYSTEM_FOREGROUND:
                self._check_start(hwnd)
                return
            if (event == EVENT_OBJECT_SHOW and self.start_mon and now - self.start_at < START_WINDOW
                    and hwnd not in self.watch and _app_window(hwnd) and process_name(hwnd) not in START_HOSTS):
                self.watch[hwnd] = now + 2.0
                self._place(hwnd, first=True)
        except Exception as e:  # never drop the check because of an odd window
            self.log(f'Controllo dello Start: {e!r}')

    def _check_start(self, hwnd):
        """If Start (or search) is in the foreground, remember which screen it's open on."""
        if not hwnd or hwnd == getattr(self, '_last_fg', None) and not getattr(self, '_last_was_start', False):
            return
        self._last_fg = hwnd
        self._last_was_start = process_name(hwnd) in START_HOSTS
        if not self._last_was_start:
            return
        # the window rectangle counts: Start opens on the screen you called it from
        rc = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        mon = user32.MonitorFromRect(ctypes.byref(rc), MONITOR_DEFAULTTONEAREST)
        if mon != self.start_mon or time.monotonic() - self.start_at > START_WINDOW:
            dev = _monitor_rects(mon)[0]
            primary = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
            if mon != primary:
                self.log(f'Start aperto sullo schermo {dev}: le app che lanci compariranno lì')
        self.start_mon = mon
        self.start_at = time.monotonic()

    def _place(self, hwnd, first=False):
        if user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) == self.start_mon:
            return
        if move_to_monitor(hwnd, self.start_mon) and first:
            self.log(f'"{_title(hwnd) or process_name(hwnd)}" aperta dallo Start: '
                     "messa sullo schermo dove l'hai cercata")

    def run(self, stop=None):
        """Thread loop: receives the Windows notifications and rechecks the windows just moved."""
        cb = WINEVENTPROC(self._on_event)  # reference kept alive while the loop runs
        hooks = [user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, cb, 0, 0, 0),
                 user32.SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, None, cb, 0, 0, 0)]
        if not all(hooks):
            self.log('Controllo dello Start: Windows non accetta gli avvisi, controllo a intervalli')
        msg = wintypes.MSG()
        try:
            while not (stop and stop.is_set()):
                user32.MsgWaitForMultipleObjects(0, None, False, 150, 0x04FF)  # QS_ALLINPUT
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                self._check_start(user32.GetForegroundWindow())  # fallback, in case a notification is lost
                now = time.monotonic()
                for hwnd, until in list(self.watch.items()):
                    if now > until or not user32.IsWindow(hwnd):
                        self.watch.pop(hwnd, None)
                    else:
                        self._place(hwnd)
        finally:
            for h in hooks:
                user32.UnhookWinEvent(h)


if __name__ == '__main__':
    for m in monitors.list_monitors():
        print(m['device'], [(_title(h) or _class(h)) for h in windows_on(m['device'])])
