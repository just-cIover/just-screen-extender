"""Lingua dell'interfaccia: italiano (il testo scritto nel codice) o inglese.

I testi restano scritti in italiano nel codice; la traduzione inglese sta in web/lingue/en.json, lo stesso file
usato dalla finestra del PC e dalla pagina web (web/lingua.js):
- "testi": frase italiana -> inglese. Le parti che cambiano (nomi, numeri) sono {} nella frase italiana; nella
  traduzione si usano {} (in ordine) oppure {1}, {2}... per cambiarne l'ordine.
- "valori": parole che finiscono dentro le frasi ("a destra", "Massima"...), tradotte anche lì.
La lingua è una sola per PC e dispositivi: la sceglie la finestra del PC o l'app e vale per tutti.
"""
import ctypes
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, 'web', 'lingue', 'en.json')
LINGUE = ('it', 'en')
NOMI = {'it': 'Italiano', 'en': 'English'}

current = 'it'
_exact = {}
_patterns = []
_values = {}


def _load():
    try:
        with open(FILE, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    _values.update(d.get('valori', {}))
    for k, v in d.get('testi', {}).items():
        if '{}' in k:
            rx = re.compile('^' + re.escape(k).replace(r'\{\}', '(.+?)') + '$', re.S)
            _patterns.append((len(k), rx, v))
        else:
            _exact[k] = v
    _patterns.sort(key=lambda p: -p[0])  # le frasi più lunghe (più precise) per prime


def default():
    """Lingua di Windows: italiano se Windows è in italiano, altrimenti inglese."""
    try:
        return 'it' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x10 else 'en'
    except (AttributeError, OSError):
        return 'it'


def use(lang):
    global current
    if lang in LINGUE:
        current = lang


def _part(s):
    """Un pezzo variabile dentro una frase: un valore noto, una frase intera o un elenco "a, b"."""
    if s in _values:
        return _values[s]
    r = _translate(s)
    if r is not None:
        return r
    for sep, out in (('  oppure  ', '  or  '), (', ', ', ')):
        if sep in s:
            return out.join(_values.get(x, _translate(x) or x) for x in s.split(sep))
    return s


def _fill(template, groups):
    parts = [_part(g) for g in groups]
    seq = iter(parts)

    def one(m):
        if m.group(1):
            i = int(m.group(1)) - 1
            return parts[i] if 0 <= i < len(parts) else ''
        return next(seq, '')
    return re.sub(r'\{(\d*)\}', one, template)


def _translate(s):
    if s in _exact:
        return _exact[s]
    if s in _values:
        return _values[s]
    for _, rx, v in _patterns:
        m = rx.match(s)
        if m:
            return _fill(v, m.groups())
    return None


def t(s, lang=None):
    """Il testo nella lingua scelta (in italiano resta com'è; senza traduzione resta in italiano)."""
    if (lang or current) == 'it' or not s:
        return s
    core = s.strip()
    if not core:
        return s
    r = _translate(core)
    if r is None:
        return s
    i = s.index(core[0])
    return s[:i] + r + s[i + len(core):]


def line(text):
    """Una riga della console "hh:mm:ss [chi] testo": si traduce solo il testo."""
    if current == 'it':
        return text
    m = re.match(r'^(\d\d:\d\d:\d\d )?(\[[^\]]{1,40}\] )?(.*)$', text, re.S)
    return (m.group(1) or '') + (m.group(2) or '') + t(m.group(3))


_load()
