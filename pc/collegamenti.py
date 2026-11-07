"""Start menu and "Send to" shortcuts, created by the program itself.

A copy downloaded from GitHub needs no setup to show up in Start search: the first time it runs (avvio.pyw or
Avvia.bat) it adds "Screen Extender" to the Start menu and to right-click > Send to. The name is the same in
every language. Shortcuts that already exist are left alone (changing language doesn't touch them): they are
only created when missing, or fixed when they point to a folder that no longer exists (the folder was moved).

The shortcuts are written by a small PowerShell script, not by Python itself: Python from the Microsoft Store
runs in a package, and Windows redirects its writes under AppData to a private copy that the Start menu never
sees. A separate PowerShell process writes to the real folders.
"""
import base64
import json
import os
import subprocess
import sys

import lingua

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'Screen Extender'
OLD_NAMES = ['Schermo Esteso']  # earlier versions used the Italian name: those shortcuts are replaced
APPDATA = os.environ.get('APPDATA', '')
START = os.path.join(APPDATA, 'Microsoft', 'Windows', 'Start Menu', 'Programs')
SENDTO = os.path.join(APPDATA, 'Microsoft', 'Windows', 'SendTo')
NO_WINDOW = 0x08000000

# Input comes from the SE_ITEMS environment variable (JSON), so no path is ever pasted into the script text.
# Prints the folders where it created or fixed a shortcut, one per line.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$sh = New-Object -ComObject WScript.Shell
function Target($p) {
    $a = $sh.CreateShortcut($p).Arguments.Trim().Trim('"')
    return $a
}
foreach ($i in ($env:SE_ITEMS | ConvertFrom-Json)) {
    if (-not (Test-Path -LiteralPath $i.folder -PathType Container)) { continue }
    $leaf = Split-Path $i.script -Leaf
    # old shortcut with the Italian name: goes away, but only if it's ours
    foreach ($old in $i.old) {
        $p = Join-Path $i.folder "$old.lnk"
        if (Test-Path -LiteralPath $p) {
            $a = Target $p
            if ($a -and (Split-Path $a -Leaf) -eq $leaf) { Remove-Item -LiteralPath $p }
        }
    }
    $p = Join-Path $i.folder "$($i.name).lnk"
    if (Test-Path -LiteralPath $p) {
        $a = Target $p
        if ($a -and (Test-Path -LiteralPath $a)) { continue }  # already there and working: never touched again
    }
    $s = $sh.CreateShortcut($p)
    $s.TargetPath = $i.target
    $s.Arguments = '"' + $i.script + '"'
    $s.WorkingDirectory = $i.cwd
    $s.IconLocation = $i.icon
    $s.Description = $i.description
    $s.Save()
    Split-Path $i.folder -Leaf
}
'''


def _pythonw():
    """pythonw.exe of the Python running the program (no console window).
    With Python from the Microsoft Store the real path changes on every update: the WindowsApps alias doesn't."""
    exe = sys.executable
    alias = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps', 'pythonw.exe')
    if 'windowsapps' in exe.lower() and os.path.exists(alias):
        return alias
    w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return w if os.path.exists(w) else exe


def sync(lang, start=START, sendto=SENDTO):
    """Creates the missing shortcuts (and fixes broken ones). Returns the folders it changed."""
    pyw = _pythonw()
    icon = os.path.join(ROOT, 'pc', 'icon.ico') + ',0'
    items = [
        {'folder': start, 'script': os.path.join(ROOT, 'avvio.pyw'), 'cwd': ROOT,
         'description': lingua.t('Usa iPad e iPhone come schermi aggiuntivi del PC', lang)},
        {'folder': sendto, 'script': os.path.join(ROOT, 'pc', 'invia.py'), 'cwd': os.path.join(ROOT, 'pc'),
         'description': lingua.t('Invia i file ai dispositivi', lang)},
    ]
    for i in items:
        i.update(name=NAME, old=OLD_NAMES, target=pyw, icon=icon)
    r = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-EncodedCommand', base64.b64encode(SCRIPT.encode('utf-16-le')).decode()],
        env=dict(os.environ, SE_ITEMS=json.dumps(items)), capture_output=True, text=True,
        creationflags=NO_WINDOW, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f'PowerShell: codice {r.returncode}')
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]
