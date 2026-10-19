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


# programmi a cui non passo mai un file trascinato (potrebbero eseguirlo, o non c'entrano):
# in quel caso il file si apre con il suo programma predefinito
NEVER_WITH = {'explorer.exe', 'cmd.exe', 'powershell.exe', 'pwsh.exe', 'powershell_ise.exe', 'wt.exe',
              'windowsterminal.exe', 'openconsole.exe', 'conhost.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe',
              'rundll32.exe', 'regsvr32.exe', 'regedit.exe', 'bash.exe', 'wsl.exe', 'python.exe', 'pythonw.exe',
              'python3.11.exe', 'pythonw3.11.exe', 'py.exe', 'pyw.exe', 'node.exe', 'java.exe', 'javaw.exe',
              'msiexec.exe', 'applicationframehost.exe', 'shellexperiencehost.exe', 'searchhost.exe',
              'startmenuexperiencehost.exe', 'textinputhost.exe', 'taskmgr.exe', 'mmc.exe', 'control.exe'}


def program_at(x, y):
    """Percorso del programma della finestra nel punto (None per desktop, Esplora file e programmi esclusi)."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, 2) or hwnd
    for h in (hwnd, root):  # app moderne: la finestra figlia è del programma vero
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        hp = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not hp:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)):
                if os.path.basename(buf.value).lower() in NEVER_WITH:
                    return None
                return buf.value
        finally:
            kernel32.CloseHandle(hp)
    return None


def _launchable(exe):
    """Le app dello Store (cartella WindowsApps) non si avviano dal loro percorso: uso il loro alias
    in %LOCALAPPDATA%\\Microsoft\\WindowsApps, se esiste."""
    if '\\windowsapps\\' not in exe.lower():
        return exe
    alias = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps', os.path.basename(exe))
    return alias if os.path.exists(alias) else None


def open_on(path, monitor, log=print, wait=12, edge_profile=None, program=None):
    """Apre il file con il suo programma (o con program, percorso di un .exe) e porta la nuova finestra,
    ingrandita, sullo schermo indicato. monitor = {'x', 'y', 'w', 'h'}. Restituisce True se la finestra
    è stata spostata. Si sposta solo una finestra che prima non esisteva: mai una già aperta dall'utente."""
    before = _every_window()
    started = False
    if program:
        exe = _launchable(program)
        try:
            if not exe:
                raise OSError('app dello Store senza collegamento')
            subprocess.Popen([exe, path])
            started = True
        except OSError as e:
            log(f'{os.path.basename(path)}: non riesco ad aprirlo con {os.path.basename(program)} ({e}), '
                'uso il suo programma')
    if not started:
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


# --- programma sotto un punto --------------------------------------------------------
kernel32 = ctypes.windll.kernel32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MonitorFromWindow.restype = wintypes.HMONITOR
kernel32.OpenProcess.restype = wintypes.HANDLE

# programmi in cui la Pencil deve scrivere (in tutti gli altri fa da dito: scorre e tocca)
INK_APPS = {'mspaint.exe', 'onenote.exe', 'onenoteim.exe', 'whiteboard.exe', 'winword.exe', 'powerpnt.exe',
            'acrobat.exe', 'acrord32.exe', 'krita.exe', 'photoshop.exe', 'inkscape.exe', 'clipstudiopaint.exe',
            'xournalpp.exe', 'journal.exe', 'sketchbook.exe', 'drawboardpdf.exe', 'snippingtool.exe',
            'screensketch.exe', 'freshpaint.exe', 'paintdotnet.exe', 'gimp.exe', 'mypaint.exe', 'blender.exe',
            'zoomit.exe', 'zoomit64.exe', 'goodnotes.exe', 'notability.exe'}
BROWSER_EXES = {'msedge.exe', 'chrome.exe', 'brave.exe', 'firefox.exe', 'opera.exe', 'vivaldi.exe'}
# altri programmi da disegno: uno per riga in programmi-penna.txt (es. "notepad3.exe"), senza toccare il codice
INK_APPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'programmi-penna.txt')


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
    """(sì/no, programma): se nel punto c'è un programma da disegno/scrittura (o un PDF nel browser)."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return False, ''
    root = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    child, top = process_name(hwnd), process_name(root)  # app moderne: la finestra figlia è del programma vero
    name = child or top
    if {child, top} & _ink_apps():
        return True, name
    return bool({child, top} & BROWSER_EXES) and '.pdf' in _title(root).lower(), name


def browser_at(x, y):
    """True se nel punto c'è una pagina di Chromium/Edge/Electron o Firefox: lì il doppio tocco non
    seleziona la parola, serve un doppio clic del mouse."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return False
    root = user32.GetAncestor(hwnd, 2) or hwnd
    classes = (_class(hwnd), _class(root))
    return any(c.startswith(('Chrome_', 'Mozilla')) for c in classes)


# --- app aperte dallo Start sullo schermo dove le hai cercate ----------------------------
START_HOSTS = {'startmenuexperiencehost.exe', 'searchhost.exe', 'searchapp.exe'}
START_WINDOW = 20  # secondi dopo lo Start in cui una finestra nuova la considero "aperta da lì"


def _monitor_rects(hmon):
    mi = monitors.MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.szDevice, mi.rcMonitor, mi.rcWork


def move_to_monitor(hwnd, hmon):
    """Sposta una finestra sullo schermo indicato mantenendo dimensioni e posizione in proporzione."""
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
    if maximized:  # un passaggio da "normale" sposta davvero anche le finestre ingrandite
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
    """Finestra principale di un programma (non menu, suggerimenti, finestre interne)."""
    if user32.GetAncestor(hwnd, 2) != hwnd or not user32.IsWindowVisible(hwnd):
        return False
    if _class(hwnd) in SKIP_CLASSES or user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    return bool(user32.GetWindowLongPtrW(hwnd, -16) & WS_CAPTION)  # GWL_STYLE


class StartFollower:
    """Ricorda su quale schermo hai aperto lo Start; le finestre nuove che compaiono subito dopo su
    un altro schermo le porta lì. Windows avvisa appena una finestra compare (niente attese);
    per 2 s la ricontrollo, perché alcuni programmi si rimettono dove erano l'ultima volta."""

    def __init__(self, log=print):
        self.log = log
        self.start_mon = None
        self.start_at = 0.0
        self.watch = {}  # finestra spostata -> fino a quando ricontrollarla

    def _on_event(self, hook, event, hwnd, obj, child, thread, ms):
        try:
            if obj != 0 or child != 0 or not hwnd:  # solo eventi della finestra intera
                return
            now = time.monotonic()
            if event == EVENT_SYSTEM_FOREGROUND:
                self._check_start(hwnd)
                return
            if (event == EVENT_OBJECT_SHOW and self.start_mon and now - self.start_at < START_WINDOW
                    and hwnd not in self.watch and _app_window(hwnd) and process_name(hwnd) not in START_HOSTS):
                self.watch[hwnd] = now + 2.0
                self._place(hwnd, first=True)
        except Exception as e:  # mai far cadere il controllo per una finestra strana
            self.log(f'Controllo dello Start: {e!r}')

    def _check_start(self, hwnd):
        """Se in primo piano c'è lo Start (o la ricerca), ricordo su quale schermo è aperto."""
        if not hwnd or process_name(hwnd) not in START_HOSTS:
            return
        # conta il rettangolo della finestra: lo Start si apre sullo schermo da cui l'hai chiamato
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
        """Ciclo del thread: riceve gli avvisi di Windows e ricontrolla le finestre appena spostate."""
        cb = WINEVENTPROC(self._on_event)  # riferimento tenuto vivo finché il ciclo gira
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
                self._check_start(user32.GetForegroundWindow())  # di riserva, se un avviso si perde
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
