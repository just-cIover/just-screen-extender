"""Uso della scheda grafica, con gli stessi contatori di Gestione attività (PDH "GPU Engine").

- 3D: disegno (desktop, finestre, browser);
- Codifica video: il codificatore hardware usato da FFmpeg per mandare gli schermi ai dispositivi;
- Memoria: memoria grafica usata (le schede integrate usano quella condivisa con il sistema).
"""
import ctypes
import re
from ctypes import wintypes

pdh = ctypes.WinDLL('pdh')
for _f in ('PdhOpenQueryW', 'PdhAddEnglishCounterW', 'PdhCollectQueryData', 'PdhGetFormattedCounterArrayW'):
    getattr(pdh, _f).restype = ctypes.c_uint32  # PDH_STATUS: i codici come PDH_MORE_DATA sono "senza segno"
PDH_FMT_DOUBLE, PDH_FMT_LARGE = 0x200, 0x400
PDH_MORE_DATA = 0x800007D2


class _VAL(ctypes.Structure):
    _fields_ = [('CStatus', wintypes.DWORD), ('value', ctypes.c_double)]


class _VAL64(ctypes.Structure):
    _fields_ = [('CStatus', wintypes.DWORD), ('value', ctypes.c_longlong)]


class _ITEM(ctypes.Structure):
    _fields_ = [('name', wintypes.LPWSTR), ('v', _VAL)]


class _ITEM64(ctypes.Structure):
    _fields_ = [('name', wintypes.LPWSTR), ('v', _VAL64)]


def _adapter_name():
    from monitors import DISPLAY_DEVICEW, user32
    d = DISPLAY_DEVICEW(); d.cb = ctypes.sizeof(d)
    i = 0
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
        if d.StateFlags & 0x4:  # scheda del monitor principale
            return d.DeviceString
        i += 1
    return 'Scheda grafica'


class Gpu:
    def __init__(self):
        self.name = _adapter_name()
        self.q = ctypes.c_void_p()
        self.ok = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) == 0
        self.engine, self.shared, self.dedicated = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        if self.ok:
            add = pdh.PdhAddEnglishCounterW
            self.ok = add(self.q, r'\GPU Engine(*)\Utilization Percentage', 0, ctypes.byref(self.engine)) == 0
            add(self.q, r'\GPU Adapter Memory(*)\Shared Usage', 0, ctypes.byref(self.shared))
            add(self.q, r'\GPU Adapter Memory(*)\Dedicated Usage', 0, ctypes.byref(self.dedicated))
            pdh.PdhCollectQueryData(self.q)  # primo campione: i valori servono dal secondo in poi

    def _array(self, counter, fmt, item):
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        r = pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if r != PDH_MORE_DATA or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return []
        arr = ctypes.cast(buf, ctypes.POINTER(item))
        return [(arr[i].name, arr[i].v.value) for i in range(count.value) if arr[i].v.CStatus in (0, 1)]

    def read(self):
        if not self.ok or pdh.PdhCollectQueryData(self.q) != 0:
            return None
        # ogni istanza è "pid_X_luid_Y_phys_0_eng_N_engtype_TIPO": sommo i processi per motore,
        # poi per ogni tipo prendo il motore più carico (come Gestione attività)
        engines = {}
        for name, v in self._array(self.engine, PDH_FMT_DOUBLE, _ITEM):
            m = re.search(r'(luid_\w+?_phys_\d+_eng_\d+)_engtype_(\w+)', name)
            if m:
                key = (m.group(1), m.group(2))
                engines[key] = engines.get(key, 0.0) + v
        by_type = {}
        for (_, kind), v in engines.items():
            by_type[kind] = max(by_type.get(kind, 0.0), v)
        mem = sum(v for _, v in self._array(self.shared, PDH_FMT_LARGE, _ITEM64)) + \
            sum(v for _, v in self._array(self.dedicated, PDH_FMT_LARGE, _ITEM64))
        return {'nome': self.name,
                'uso': round(min(100, max(by_type.values(), default=0))),
                '3d': round(min(100, by_type.get('3D', 0))),
                # le schede Intel fanno compressione e decompressione video sullo stesso motore, che
                # Windows chiama "VideoDecode"; altre schede hanno un "VideoEncode" separato
                'codifica': round(min(100, max(by_type.get(k, 0) for k in ('VideoEncode', 'VideoDecode', 'VideoCodec')))),
                'memGb': round(mem / 2**30, 1)}


if __name__ == '__main__':
    import time
    g = Gpu()
    for _ in range(3):
        time.sleep(1)
        print(g.read())
