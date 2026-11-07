"""Right-click on one or more files > Send to > Screen Extender.

Copies the files into the exchange folder and notifies the connected devices, which can
open or save them. Runs with pythonw (no window): messages appear in a dialog box.
"""
import ctypes
import json
import os
import sys
import urllib.request

import lingua

HERE = os.path.dirname(os.path.abspath(__file__))


def message(text, error=False):
    ctypes.windll.user32.MessageBoxW(None, lingua.t(text), lingua.t('Screen Extender'), 0x10 if error else 0x40)


def main():
    cfg = {}
    try:
        with open(os.path.join(os.path.dirname(HERE), 'data', 'config.json'), encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    lingua.use(cfg.get('lingua') or lingua.default())
    paths = [os.path.abspath(p) for p in sys.argv[1:] if os.path.isfile(p)]
    if not paths:
        message('Seleziona uno o più file (le cartelle non si possono inviare).', True)
        return
    port = cfg.get('porta_locale', 8769)  # port reserved for this PC
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/locale',
                                 data=json.dumps({'percorsi': paths}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            res = json.load(r)
    except OSError:
        message('Screen Extender non è avviato.\nAvvialo dal menu Start e riprova.', True)
        return
    n, d = len(res['inviati']), res['dispositivi']
    if d:
        message(f'{n} file inviati a {d} dispositivi collegati.')
    else:
        message(f'{n} file messi nella cartella condivisa.\n'
                'Nessun dispositivo è collegato ora: li troverai nel pannello File.')


if __name__ == '__main__':
    main()
