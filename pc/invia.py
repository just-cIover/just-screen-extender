"""Clic destro su uno o più file > Invia a > Schermo Esteso.

Copia i file nella cartella di scambio e avvisa i dispositivi collegati, che li possono
aprire o salvare. Gira con pythonw (senza finestra): i messaggi compaiono in un riquadro.
"""
import ctypes
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def message(text, error=False):
    ctypes.windll.user32.MessageBoxW(None, text, 'Schermo Esteso', 0x10 if error else 0x40)


def main():
    paths = [os.path.abspath(p) for p in sys.argv[1:] if os.path.isfile(p)]
    if not paths:
        message('Seleziona uno o più file (le cartelle non si possono inviare).', True)
        return
    port = 8769  # porta riservata a questo PC
    try:
        with open(os.path.join(os.path.dirname(HERE), 'data', 'config.json'), encoding='utf-8') as f:
            port = json.load(f).get('porta_locale', port)
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/locale',
                                 data=json.dumps({'percorsi': paths}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            res = json.load(r)
    except OSError:
        message('Schermo Esteso non è avviato.\nAvvialo dal menu Start e riprova.', True)
        return
    n, d = len(res['inviati']), res['dispositivi']
    if d:
        message(f'{n} file inviati a {d} dispositivi collegati.')
    else:
        message(f'{n} file messi nella cartella condivisa.\n'
                'Nessun dispositivo è collegato ora: li troverai nel pannello File.')


if __name__ == '__main__':
    main()
