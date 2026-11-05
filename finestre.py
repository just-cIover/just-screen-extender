"""Spostare le finestre di uno schermo che si spegne sul monitor principale, in secondo piano.

Le finestre vengono messe sotto tutte le altre e non prendono il focus, così non coprono
quello che c'è già sul monitor principale. Posizione e dimensioni vengono riportate in
proporzione dentro l'area di lavoro del monitor principale.
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

import monitors  # imposta anche la DPI awareness

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED, SW_SHOWNOACTIVATE, SW_SHOWMINNOACTIVE = 2, 3, 4, 7
HWND_BOTTOM = ctypes.c_void_p(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
WS_EX_TOOLWINDOW = 0x80
GWL_EXSTYLE = -20
DWMWA_CLOAKED = 14
MONITOR_DEFAULTTONEAREST = 2

# finestre di sistema da non toccare (sfondo, barre delle applicazioni)
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
    hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # 1 = monitor principale
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
    """Finestre normali (visibili, anche ridotte a icona) che stanno sullo schermo indicato,
    dall'alto verso il basso."""
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
            return True  # finestre nascoste dalle app moderne
        wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return True
        if wp.showCmd == SW_SHOWMINIMIZED:
            rc = wp.rcNormalPosition  # ridotta a icona: conta dove tornerebbe
        else:
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
        if _monitor_device(rc) == device:
            found.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return found


def move_to_primary(device, log=print):
    """Porta sul monitor principale, sotto tutte le altre, le finestre dello schermo indicato."""
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
        # rcNormalPosition è in coordinate dell'area di lavoro: la riporto a coordinate dello schermo
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
            # in fondo alla pila: dall'alto verso il basso, così l'ordine tra loro resta lo stesso
            user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            moved += 1
        else:
            log(f'Finestra "{_title(hwnd)}" non spostata (forse è di un programma avviato come amministratore)')
    if fg and user32.GetForegroundWindow() != fg:
        user32.SetForegroundWindow(fg)
    return moved



# --- aprire un file direttamente sullo schermo di un dispositivo -----------------------
BROWSERS = {'msedge.exe': '--new-window', 'chrome.exe': '--new-window', 'brave.exe': '--new-window',
            'firefox.exe': '-new-window'}
SW_MAXIMIZE = 3


def _all_windows():
    """Finestre normali visibili, in qualsiasi schermo."""
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
    """Tutte le finestre principali che esistono, anche nascoste o su altri desktop."""
    found = set()
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proc(lambda h, _: found.add(h) or True), 0)
    return found


def _associated_exe(path):
    """Programma che Windows usa per aprire questo tipo di file (es. msedge.exe per i PDF)."""
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
    """Apre il file con il suo programma e porta la nuova finestra, ingrandita, sullo schermo indicato.
    monitor = {'x', 'y', 'w', 'h'}. Restituisce True se la finestra è stata spostata.
    Si sposta solo una finestra che prima non esisteva: mai una finestra già aperta dall'utente."""
    before = _every_window()
    exe = _associated_exe(path)
    name = os.path.basename(exe or '').lower()
    if name == 'msedge.exe' and edge_profile:
        # profilo separato dell'app (senza account): il file non finisce tra le schede del tuo Edge
        subprocess.Popen([exe, '--user-data-dir=' + edge_profile, '--new-window', '--no-first-run',
                          '--no-default-browser-check', '--disable-sync',
                          '--disable-features=msImplicitSignin,msEdgeImplicitSignin,msAutoSignIn', path])
    elif name in BROWSERS:  # altri browser: almeno una finestra nuova invece di una scheda
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
    time.sleep(0.4)  # lascio finire al programma il suo posizionamento iniziale
    (_, _, _, _), (offx, offy) = _primary_work()
    x, y, w, h = monitor['x'], monitor['y'], monitor['w'], monitor['h']
    wp = WINDOWPLACEMENT(); wp.length = ctypes.sizeof(wp)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    m = 40  # posizione "normale" (quando la finestra non è ingrandita): quasi tutto lo schermo
    wp.rcNormalPosition = wintypes.RECT(x + m - offx, y + m - offy, x + w - m - offx, y + h - m - offy)
    wp.showCmd = SW_SHOWNOACTIVATE
    wp.flags = 0
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)
    return True

if __name__ == '__main__':
    for m in monitors.list_monitors():
        print(m['device'], [(_title(h) or _class(h)) for h in windows_on(m['device'])])
