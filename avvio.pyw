"""Avvio di Schermo Esteso dal menu Start: server senza terminale + finestra di controllo.

- Se il server non è già acceso, lo avvia nascosto (pythonw server.py --ui).
- Apre la finestra di controllo in Edge, come app senza barre (profilo separato nella
  cartella data\\.edge-profile, senza account né sincronizzazione: non si mescola con il tuo Edge).
- Chiudendo la finestra, dopo pochi secondi il server si chiude da solo.
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


def message(text):
    ctypes.windll.user32.MessageBoxW(None, text, 'Schermo Esteso', 0x10)


def port():
    """Porta riservata a questo PC (la finestra di controllo non passa dalla rete)."""
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
                          # niente accesso automatico all'account Microsoft di Windows né sincronizzazione:
                          # questo profilo serve solo per la finestra dell'app
                          '--disable-sync',
                          # la finestra non va mai messa in pausa (coperta, ridotta a icona, PC carico): se si ferma
                          # perde il collegamento con il server
                          '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                          '--disable-backgrounding-occluded-windows',
                          '--disable-features=msImplicitSignin,msEdgeImplicitSignin,msAutoSignIn,'
                          'CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,msEfficiencyMode'],
                         creationflags=DETACHED)
    else:
        webbrowser.open(url)


if __name__ == '__main__':
    main()
