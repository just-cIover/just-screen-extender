"""Real Windows drag and drop (OLE drag and drop) of a file onto a point of the screen.

This way the program under the point decides what to do with it, as when you drag with the mouse:
File Explorer and the desktop copy it there, Obsidian puts it in the vault folder, Word and chat apps
insert it, browsers upload it into the page.

How it works: the mouse button is pressed on a small invisible window of ours (so nothing gets
clicked by mistake), the drag starts with the file, the cursor moves to the point and the
button is released: Windows hands the file to the program that's there.
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

import monitors  # also sets DPI awareness

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
        # absolute coordinates over the whole desktop (0..65535), even with screens to the left or above
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
    """The "file" object File Explorer uses when you drag that file."""
    folder, name = os.path.split(os.path.abspath(path))
    desktop = shell.SHGetDesktopFolder()
    _, pidl_folder, _ = desktop.ParseDisplayName(0, None, folder)
    parent = desktop.BindToObject(pidl_folder, None, shell.IID_IShellFolder)
    _, pidl_item, _ = parent.ParseDisplayName(0, None, name)
    return parent.GetUIObjectOf(0, [pidl_item], pythoncom.IID_IDataObject, 0)[1]


def _grip_window(x, y):
    """Tiny almost transparent window under the cursor: it receives the button press instead of the program."""
    cls = 'ScreenExtenderTrascina'
    wc = win32gui.WNDCLASS()
    wc.lpszClassName = cls
    wc.lpfnWndProc = {}
    wc.hInstance = win32gui.GetModuleHandle(None)
    try:
        win32gui.RegisterClass(wc)
    except win32gui.error:
        pass  # already registered
    ex = win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
    hwnd = win32gui.CreateWindowEx(ex, cls, '', win32con.WS_POPUP, x - 20, y - 20, 40, 40, 0, 0, wc.hInstance, None)
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 1, win32con.LWA_ALPHA)  # invisible but "touchable"
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    win32gui.UpdateWindow(hwnd)
    return hwnd


def drop_at(path, x, y):
    """Drags the file and drops it at point (x, y) in screen pixels.
    Returns the effect chosen by the program: 'copiato', 'spostato', 'collegato' or None (nobody took it)."""
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
                # while Windows drags: remove the tiny window and move the cursor to the point, then release
                time.sleep(0.15)
                # the tiny window moves off screen (closing it now would interrupt the drag)
                win32gui.SetWindowPos(hwnd, 0, -32000, -32000, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                                      | win32con.SWP_NOACTIVATE)
                for k in range(6):
                    _mouse(MOVE, x + (6 - k), y + (6 - k))
                    time.sleep(0.04)
                _mouse(MOVE, x, y)
                time.sleep(0.35)  # the program decides whether to accept (DragOver)
                _mouse(LEFTUP)

            threading.Thread(target=guide, daemon=True).start()
            effect = pythoncom.DoDragDrop(data, source, DROPEFFECT_COPY | DROPEFFECT_LINK)
            result['effect'] = {DROPEFFECT_COPY: 'copiato', DROPEFFECT_MOVE: 'spostato',
                                DROPEFFECT_LINK: 'collegato'}.get(effect & 7)
        except Exception as e:  # never leave the button pressed
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
