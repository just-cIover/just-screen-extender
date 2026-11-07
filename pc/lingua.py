"""Interface language: Italian (the text written in the code) or English.

Texts stay written in Italian in the code; the English translation is in web/lingue/en.json, the same file
used by the PC window and the web page (web/lingua.js):
- "testi": Italian sentence -> English. Changing parts (names, numbers) are {} in the Italian sentence; the
  translation uses {} (in order) or {1}, {2}... to change their order.
- "valori": words that end up inside sentences ("a destra", "Massima"...), translated there too.
There's one language for the PC and the devices: the PC window or the app picks it and it applies to everyone.
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
    _patterns.sort(key=lambda p: -p[0])  # longest (most specific) sentences first


def default():
    """Windows language: Italian if Windows is in Italian, English otherwise."""
    try:
        return 'it' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x10 else 'en'
    except (AttributeError, OSError):
        return 'it'


def use(lang):
    global current
    if lang in LINGUE:
        current = lang


def _part(s):
    """A variable piece inside a sentence: a known value, a whole sentence or a list "a, b"."""
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
    """The text in the chosen language (in Italian it stays as it is; without a translation it stays in Italian)."""
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
    """A console line "hh:mm:ss [who] text": only the text is translated."""
    if current == 'it':
        return text
    m = re.match(r'^(\d\d:\d\d:\d\d )?(\[[^\]]{1,40}\] )?(.*)$', text, re.S)
    return (m.group(1) or '') + (m.group(2) or '') + t(m.group(3))


_load()
