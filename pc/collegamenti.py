"""Start menu and "Send to" shortcuts, created by the program itself.

A copy downloaded from GitHub needs no setup to show up in Start search: the first time it runs (avvio.pyw or
Avvia.bat) it adds "Schermo Esteso" to the Start menu and to right-click > Send to. The shortcuts always point to
the folder the program runs from (moving the folder fixes them on the next start), and their name follows the
interface language: "Schermo Esteso" in Italian, "Screen Extender" in English.
"""
import os
import sys

import lingua

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = {'it': 'Schermo Esteso', 'en': 'Screen Extender'}
APPDATA = os.environ.get('APPDATA', '')
START = os.path.join(APPDATA, 'Microsoft', 'Windows', 'Start Menu', 'Programs')
SENDTO = os.path.join(APPDATA, 'Microsoft', 'Windows', 'SendTo')


def _pythonw():
    """pythonw.exe of the Python running the program (no console window).
    With Python from the Microsoft Store the real path changes on every update: the WindowsApps alias doesn't."""
    exe = sys.executable
    alias = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps', 'pythonw.exe')
    if 'windowsapps' in exe.lower() and os.path.exists(alias):
        return alias
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return w if os.path.exists(w) else exe


def sync(lang):
    """Creates or fixes the shortcuts for this folder and language. Returns the names it changed."""
    import pythoncom
    from win32com.client import Dispatch
    pythoncom.CoInitialize()
    try:
        shell = Dispatch('WScript.Shell')
        pyw = _pythonw()
        icon = os.path.join(ROOT, 'pc', 'icon.ico') + ',0'
        name = NAMES.get(lang, NAMES['it'])
        changed = []
        for folder, script, cwd, what in (
                (START, os.path.join(ROOT, 'avvio.pyw'), ROOT, 'Usa iPad e iPhone come schermi aggiuntivi del PC'),
                (SENDTO, os.path.join(ROOT, 'pc', 'invia.py'), os.path.join(ROOT, 'pc'), 'Invia i file ai dispositivi')):
            if not os.path.isdir(folder):
                continue
            # the shortcut in the other language goes away, but only if it's ours
            for other in NAMES.values():
                p = os.path.join(folder, other + '.lnk')
                if other != name and os.path.exists(p) and \
                        os.path.basename(script).lower() in shell.CreateShortcut(p).Arguments.lower():
                    os.remove(p)
            p = os.path.join(folder, name + '.lnk')
            args = '"%s"' % script
            s = shell.CreateShortcut(p)
            if (os.path.exists(p) and s.TargetPath.lower() == pyw.lower() and s.Arguments == args
                    and s.IconLocation.lower() == icon.lower()):
                continue
            s.TargetPath = pyw
            s.Arguments = args
            s.WorkingDirectory = cwd
            s.IconLocation = icon
            s.Description = lingua.t(what, lang)
            s.Save()
            changed.append(os.path.basename(folder))
        return changed
    finally:
        pythoncom.CoUninitialize()
