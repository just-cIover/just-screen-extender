"""Trascinamento vero di Windows (OLE drag and drop) di un file in un punto dello schermo.

Così è il programma sotto il punto a decidere cosa farne, come quando trascini con il mouse:
Esplora file e il desktop lo copiano lì, Obsidian lo mette nella cartella del vault, Word e le chat
lo inseriscono, i browser lo caricano nella pagina.

Come funziona: il tasto del mouse si preme su una piccola finestra invisibile nostra (così non si
clicca niente per sbaglio), parte il trascinamento con il file, il cursore si sposta sul punto e il
tasto si rilascia: Windows consegna il file al programma che sta lì.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

import pythoncom
import win32con
import win32gui
from win32com.server.util import wrap
from win32com.shell import shell

import monitors  # imposta anche la DPI awareness

user32 = ctypes.windll.user32

MK_LBUTTON = 0x1
S_OK = 0
DRAGDROP_S_DROP, DRAGDROP_S_CANCEL, DRAGDROP_S_USEDEFAULTCURSORS = 0x40100, 0x40101, 0x40102
DROPEFFECT_COPY, DROPEFFECT_MOVE, DROPEFFECT_LINK = 1, 2, 4
LEFTDOWN, LEFTUP, MOVE, ABSOLUTE, VIRTUALDESK = 0x2, 0x4, 0x1, 0x8000, 0x4000


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _Input(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('mi', _MouseInput), ('pad', ctypes.c_byte * 32)]
    _fields_ = [('type', wintypes.DWORD), ('u', _U)]


def _mouse(flags, x=0, y=0):
    i = _Input(type=0)
    if flags & MOVE:
        # coordinate assolute su tutto il desktop (0..65535), anche con schermi a sinistra o sopra
        vx, vy, vw, vh = monitors.virtual_screen()
        x = round((x - vx) * 65535 / max(vw - 1, 1))
        y = round((y - vy) * 65535 / max(vh - 1, 1))
        flags |= ABSOLUTE | VIRTUALDESK
    i.u.mi = _MouseInput(x, y, 0, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_Input))


class _DropSource:
    _com_interfaces_ = [pythoncom.IID_IDropSource]
    _public_methods_ = ['QueryContinueDrag', 'GiveFeedback']

    def QueryContinueDrag(self, escape, keystate):
        if escape:
            return DRAGDROP_S_CANCEL
        if not keystate & MK_LBUTTON:
            return DRAGDROP_S_DROP
        return S_OK

    def GiveFeedback(self, effect):
        return DRAGDROP_S_USEDEFAULTCURSORS


def _data_object(path):
    """L'oggetto "file" che Esplora file usa quando trascini quel file."""
    folder, name = os.path.split(os.path.abspath(path))
    desktop = shell.SHGetDesktopFolder()
    _, pidl_folder, _ = desktop.ParseDisplayName(0, None, folder)
    parent = desktop.BindToObject(pidl_folder, None, shell.IID_IShellFolder)
    _, pidl_item, _ = parent.ParseDisplayName(0, None, name)
    return parent.GetUIObjectOf(0, [pidl_item], pythoncom.IID_IDataObject, 0)[1]


def _grip_window(x, y):
    """Finestrella quasi trasparente sotto il cursore: riceve la pressione del tasto al posto del programma."""
    cls = 'SchermoEstesoTrascina'
    wc = win32gui.WNDCLASS()
    wc.lpszClassName = cls
    wc.lpfnWndProc = {}
    wc.hInstance = win32gui.GetModuleHandle(None)
    try:
        win32gui.RegisterClass(wc)
    except win32gui.error:
        pass  # già registrata
    ex = win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
    hwnd = win32gui.CreateWindowEx(ex, cls, '', win32con.WS_POPUP, x - 20, y - 20, 40, 40, 0, 0, wc.hInstance, None)
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 1, win32con.LWA_ALPHA)  # invisibile ma "toccabile"
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.UpdateWindow(hwnd)
    return hwnd


def drop_at(path, x, y):
    """Trascina il file e lo lascia nel punto (x, y) in pixel dello schermo.
    Restituisce l'effetto scelto dal programma: 'copiato', 'spostato', 'collegato' o None (nessuno l'ha preso)."""
    result = {}

    def run():
        pythoncom.OleInitialize()
        hwnd = None
        old = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(old))
        try:
            data = _data_object(path)
            source = wrap(_DropSource(), pythoncom.IID_IDropSource)
            hwnd = _grip_window(x, y)
            win32gui.PumpWaitingMessages()
            user32.SetCursorPos(x, y)
            _mouse(LEFTDOWN)
            time.sleep(0.05)
            win32gui.PumpWaitingMessages()

            def guide():
                # mentre Windows trascina: tolgo la finestrella e muovo il cursore sul punto, poi rilascio
                time.sleep(0.15)
                # la finestrella si sposta fuori dallo schermo (chiuderla ora interromperebbe il trascinamento)
                win32gui.SetWindowPos(hwnd, 0, -32000, -32000, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                                      | win32con.SWP_NOACTIVATE)
                for k in range(6):
                    _mouse(MOVE, x + (6 - k), y + (6 - k))
                    time.sleep(0.04)
                _mouse(MOVE, x, y)
                time.sleep(0.35)  # il programma decide se accettare (DragOver)
                _mouse(LEFTUP)

            threading.Thread(target=guide, daemon=True).start()
            effect = pythoncom.DoDragDrop(data, source, DROPEFFECT_COPY | DROPEFFECT_LINK)
            result['effect'] = {DROPEFFECT_COPY: 'copiato', DROPEFFECT_MOVE: 'spostato',
                                DROPEFFECT_LINK: 'collegato'}.get(effect & 7)
        except Exception as e:  # mai lasciare il tasto premuto
            result['error'] = e
            _mouse(LEFTUP)
        finally:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
            user32.SetCursorPos(old.x, old.y)
            pythoncom.CoUninitialize()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(20)
    if 'error' in result:
        raise result['error']
    return result.get('effect')
