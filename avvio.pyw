"""Starts Schermo Esteso from the Start menu: server without a terminal + control window.

- If the server isn't running yet, starts it hidden (pythonw server.py --ui).
- Opens the control window in Edge as an app without toolbars (separate profile in the
  data\\.edge-profile folder, no account or sync: it doesn't mix with your own Edge).
- When the window is closed, the server shuts itself down after a few seconds.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'pc')
DATA = os.path.join(HERE, 'data')
NO_WINDOW, DETACHED = 0x08000000, 0x00000008
sys.path.insert(0, APP)
import lingua  # noqa: E402  (lives in the pc folder)


def message(text):
    try:
        with open(os.path.join(DATA, 'config.json'), encoding='utf-8') as f:
            lang = json.load(f).get('lingua')
    except (OSError, ValueError):
        lang = None
    lingua.use(lang or lingua.default())
    ctypes.windll.user32.MessageBoxW(None, lingua.t(text), lingua.t('Schermo Esteso'), 0x10)


def port():
    """Port reserved for this PC (the control window doesn't go through the network)."""
    try:
        with open(os.path.join(DATA, 'config.json'), encoding='utf-8') as f:
            return json.load(f).get('porta_locale', 8769)
    except (OSError, ValueError):
        return 8769


def alive(base):
    try:
        with urllib.request.urlopen(base + '/pc/ping', timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def edge():
    for root in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if root:
            p = os.path.join(root, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
            if os.path.exists(p):
                return p
    return None


def main():
    base = f'http://127.0.0.1:{port()}'
    if not alive(base):
        pyw = sys.executable
        if os.path.basename(pyw).lower() == 'python.exe':
            pyw = os.path.join(os.path.dirname(pyw), 'pythonw.exe')
        subprocess.Popen([pyw, os.path.join(APP, 'server.py'), '--ui'], cwd=APP,
                         creationflags=NO_WINDOW | DETACHED, close_fds=True)
        for _ in range(60):
            time.sleep(0.25)
            if alive(base):
                break
        else:
            message('Schermo Esteso non è partito.\nDettagli nel file data\\server.log nella cartella dell\'app.')
            return
    url = base + '/pc'
    exe = edge()
    if exe:
        subprocess.Popen([exe, f'--app={url}', '--user-data-dir=' + os.path.join(DATA, '.edge-profile'),
                          '--window-size=1280,800', '--no-first-run', '--no-default-browser-check',
                          # no automatic sign-in to the Windows Microsoft account and no sync:
                          # this profile is only for the app window
                          '--disable-sync',
                          # the window must never be paused (covered, minimized, PC busy): if it stops
                          # it loses the connection to the server
                          '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                          '--disable-backgrounding-occluded-windows',
                          '--disable-features=msImplicitSignin,msEdgeImplicitSignin,msAutoSignIn,'
                          'CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,msEfficiencyMode'],
                         creationflags=DETACHED)
    else:
        webbrowser.open(url)


if __name__ == '__main__':
    main()
