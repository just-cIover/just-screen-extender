"""Monitor di Windows: elenco, schermi virtuali, risoluzione, ridimensionamento (DPI)
e indice di cattura per FFmpeg (ddagrab)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

# Coordinate in pixel fisici anche con lo zoom di Windows al 150-200%
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    pass


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD), ('szDevice', wintypes.WCHAR * 32)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('DeviceName', wintypes.WCHAR * 32), ('DeviceString', wintypes.WCHAR * 128),
                ('StateFlags', wintypes.DWORD), ('DeviceID', wintypes.WCHAR * 128), ('DeviceKey', wintypes.WCHAR * 128)]


def _device_info(name):
    """Nome della scheda video e del monitor collegati a \\\\.\\DISPLAYn."""
    a = DISPLAY_DEVICEW(); a.cb = ctypes.sizeof(a)
    m = DISPLAY_DEVICEW(); m.cb = ctypes.sizeof(m)
    adapter = monitor = mid = ''
    i = 0
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(a), 0):
        if a.DeviceName == name:
            adapter = a.DeviceString
            if user32.EnumDisplayDevicesW(name, 0, ctypes.byref(m), 0):
                monitor, mid = m.DeviceString, m.DeviceID
            break
        i += 1
    return adapter, monitor, mid


def list_monitors():
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rc, lp):
        mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        adapter, monitor, mid = _device_info(mi.szDevice)
        out.append({'device': mi.szDevice, 'x': r.left, 'y': r.top, 'w': r.right - r.left, 'h': r.bottom - r.top,
                    'primary': bool(mi.dwFlags & 1), 'adapter': adapter, 'monitor': monitor, 'id': mid})
        return True

    user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    return out


def is_virtual(m):
    s = ' '.join((m['adapter'], m['monitor'], m['id'])).lower()
    return any(k in s for k in ('virtual display', 'mttvdd', 'mtt1337', 'vdd'))


def list_virtual():
    """Gli schermi virtuali (uno per dispositivo), in ordine di nome."""
    return sorted((m for m in list_monitors() if is_virtual(m)), key=lambda m: _num(m['device']))


def _num(device):
    digits = ''.join(ch for ch in device if ch.isdigit())
    return int(digits) if digits else 0


def by_device(device):
    for m in list_monitors():
        if m['device'] == device:
            return m
    return None


def virtual_screen():
    """Rettangolo che contiene tutti i monitor (serve per il mouse in coordinate assolute)."""
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


# --- indice di cattura (DXGI) ----------------------------------------------
# ddagrab di FFmpeg cattura l'uscita "output_idx" della scheda video predefinita (DXGI, scheda 0).
# Qui trovo quale uscita corrisponde a \\.\DISPLAYn.

class _GUID(ctypes.Structure):
    _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16), ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]


class _OUTPUT_DESC(ctypes.Structure):
    _fields_ = [('DeviceName', wintypes.WCHAR * 32), ('rc', wintypes.RECT), ('attached', wintypes.BOOL),
                ('rotation', ctypes.c_int), ('monitor', ctypes.c_void_p)]


_IID_FACTORY1 = _GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))


def _vcall(obj, index, restype, *argtypes):
    table = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def dxgi_outputs():
    """{'\\\\.\\DISPLAYn': indice dell'uscita sulla scheda 0}."""
    out = {}
    factory = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(_IID_FACTORY1), ctypes.byref(factory)) != 0:
        return out
    try:
        adapter = ctypes.c_void_p()
        if _vcall(factory, 7, ctypes.c_long, ctypes.c_uint, ctypes.c_void_p)(factory, 0, ctypes.byref(adapter)) != 0:
            return out
        try:
            i = 0
            while True:
                output = ctypes.c_void_p()
                if _vcall(adapter, 7, ctypes.c_long, ctypes.c_uint, ctypes.c_void_p)(adapter, i, ctypes.byref(output)) != 0:
                    break
                d = _OUTPUT_DESC()
                _vcall(output, 7, ctypes.c_long, ctypes.c_void_p)(output, ctypes.byref(d))
                _vcall(output, 2, ctypes.c_ulong)(output)
                out[d.DeviceName] = i
                i += 1
        finally:
            _vcall(adapter, 2, ctypes.c_ulong)(adapter)
    finally:
        _vcall(factory, 2, ctypes.c_ulong)(factory)
    return out


# --- risoluzione -------------------------------------------------------------

class DEVMODEW(ctypes.Structure):
    _fields_ = [('dmDeviceName', wintypes.WCHAR * 32), ('dmSpecVersion', wintypes.WORD),
                ('dmDriverVersion', wintypes.WORD), ('dmSize', wintypes.WORD), ('dmDriverExtra', wintypes.WORD),
                ('dmFields', wintypes.DWORD), ('dmPositionX', wintypes.LONG), ('dmPositionY', wintypes.LONG),
                ('dmDisplayOrientation', wintypes.DWORD), ('dmDisplayFixedOutput', wintypes.DWORD),
                ('dmColor', ctypes.c_short), ('dmDuplex', ctypes.c_short), ('dmYResolution', ctypes.c_short),
                ('dmTTOption', ctypes.c_short), ('dmCollate', ctypes.c_short), ('dmFormName', wintypes.WCHAR * 32),
                ('dmLogPixels', wintypes.WORD), ('dmBitsPerPel', wintypes.DWORD), ('dmPelsWidth', wintypes.DWORD),
                ('dmPelsHeight', wintypes.DWORD), ('dmDisplayFlags', wintypes.DWORD),
                ('dmDisplayFrequency', wintypes.DWORD), ('dmICMMethod', wintypes.DWORD),
                ('dmICMIntent', wintypes.DWORD), ('dmMediaType', wintypes.DWORD), ('dmDitherType', wintypes.DWORD),
                ('dmReserved1', wintypes.DWORD), ('dmReserved2', wintypes.DWORD), ('dmPanningWidth', wintypes.DWORD),
                ('dmPanningHeight', wintypes.DWORD)]


DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x80000, 0x100000, 0x400000
CDS_UPDATEREGISTRY = 0x1


def list_modes(device):
    """Risoluzioni disponibili per lo schermo: {(larghezza, altezza)}."""
    modes = set()
    i = 0
    while True:
        dm = DEVMODEW(); dm.dmSize = ctypes.sizeof(dm)
        if not user32.EnumDisplaySettingsW(device, i, ctypes.byref(dm)):
            break
        modes.add((dm.dmPelsWidth, dm.dmPelsHeight))
        i += 1
    return modes


def set_mode(device, w, h):
    """Cambia la risoluzione di uno schermo. Restituisce True se riuscito."""
    dm = DEVMODEW(); dm.dmSize = ctypes.sizeof(dm)
    if not user32.EnumDisplaySettingsW(device, -1, ctypes.byref(dm)):  # -1 = impostazione attuale
        return False
    dm.dmPelsWidth, dm.dmPelsHeight = w, h
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    return user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None) == 0


# --- ridimensionamento (100%, 150%, 200%...) ---------------------------------
# Windows non ha una funzione documentata: si usa DisplayConfigGet/SetDeviceInfo con i tipi -3/-4,
# come fa la pagina Impostazioni. I valori sono relativi a quello "consigliato" per lo schermo.

DPI_VALUES = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]


class _LUID(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.LONG)]


class _HEADER(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('size', ctypes.c_uint32), ('adapterId', _LUID), ('id', ctypes.c_uint32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [('header', _HEADER), ('viewGdiDeviceName', wintypes.WCHAR * 32)]


class _DPI_GET(ctypes.Structure):
    _fields_ = [('header', _HEADER), ('minRel', ctypes.c_int), ('curRel', ctypes.c_int), ('maxRel', ctypes.c_int)]


class _DPI_SET(ctypes.Structure):
    _fields_ = [('header', _HEADER), ('rel', ctypes.c_int)]


class _PATH(ctypes.Structure):
    _fields_ = [('srcAdapter', _LUID), ('srcId', ctypes.c_uint32), ('srcMode', ctypes.c_uint32),
                ('srcFlags', ctypes.c_uint32), ('_target', ctypes.c_ubyte * 48), ('flags', ctypes.c_uint32)]


class _MODE(ctypes.Structure):
    _fields_ = [('_raw', ctypes.c_ubyte * 64)]


def _source_of(device):
    np_, nm = ctypes.c_uint32(), ctypes.c_uint32()
    if user32.GetDisplayConfigBufferSizes(2, ctypes.byref(np_), ctypes.byref(nm)) != 0:  # 2 = solo percorsi attivi
        return None
    paths, modes = (_PATH * np_.value)(), (_MODE * nm.value)()
    if user32.QueryDisplayConfig(2, ctypes.byref(np_), paths, ctypes.byref(nm), modes, None) != 0:
        return None
    for p in paths[:np_.value]:
        sn = _SOURCE_NAME()
        sn.header.type, sn.header.size = 1, ctypes.sizeof(sn)  # 1 = nome GDI della sorgente
        sn.header.adapterId, sn.header.id = p.srcAdapter, p.srcId
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(sn)) == 0 and sn.viewGdiDeviceName == device:
            return p.srcAdapter, p.srcId
    return None


def _dpi_info(device):
    src = _source_of(device)
    if not src:
        return None
    g = _DPI_GET()
    g.header.type, g.header.size = -3, ctypes.sizeof(g)
    g.header.adapterId, g.header.id = src
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(g)) != 0:
        return None
    return src, g


def get_scale(device):
    """Ridimensionamento attuale in percento (es. 200), oppure None."""
    info = _dpi_info(device)
    if not info:
        return None
    _, g = info
    rec = abs(g.minRel)
    idx = max(0, min(rec + g.curRel, len(DPI_VALUES) - 1))
    return DPI_VALUES[idx]


def set_scale(device, percent):
    info = _dpi_info(device)
    if not info or percent not in DPI_VALUES:
        return False
    src, g = info
    rel = DPI_VALUES.index(percent) - abs(g.minRel)
    rel = max(g.minRel, min(rel, g.maxRel))
    s = _DPI_SET()
    s.header.type, s.header.size = -4, ctypes.sizeof(s)
    s.header.adapterId, s.header.id = src
    s.rel = rel
    return user32.DisplayConfigSetDeviceInfo(ctypes.byref(s)) == 0



# --- accendere / spegnere un singolo schermo virtuale -------------------------
# Uso QueryDisplayConfig/SetDisplayConfig (come Impostazioni > Schermo): si tocca solo il percorso
# dello schermo interessato, gli altri restano come sono. Windows ricorda per ogni schermo
# posizione, risoluzione e ridimensionamento, e li riapplica quando lo riaccendo.

QDC_ALL_PATHS, QDC_ONLY_ACTIVE = 1, 2
PATH_ACTIVE = 1
MODE_IDX_INVALID = 0xFFFFFFFF
SDC_APPLY, SDC_USE_SUPPLIED, SDC_SAVE, SDC_ALLOW_CHANGES = 0x80, 0x20, 0x200, 0x400


class _SRC_INFO(ctypes.Structure):
    _fields_ = [('adapterId', _LUID), ('id', ctypes.c_uint32), ('modeInfoIdx', ctypes.c_uint32),
                ('statusFlags', ctypes.c_uint32)]


class _TGT_INFO(ctypes.Structure):
    _fields_ = [('adapterId', _LUID), ('id', ctypes.c_uint32), ('modeInfoIdx', ctypes.c_uint32),
                ('outputTechnology', ctypes.c_uint32), ('rotation', ctypes.c_uint32), ('scaling', ctypes.c_uint32),
                ('refreshNum', ctypes.c_uint32), ('refreshDen', ctypes.c_uint32), ('scanLineOrdering', ctypes.c_uint32),
                ('targetAvailable', wintypes.BOOL), ('statusFlags', ctypes.c_uint32)]


class _FULLPATH(ctypes.Structure):
    _fields_ = [('src', _SRC_INFO), ('tgt', _TGT_INFO), ('flags', ctypes.c_uint32)]


class _SOURCE_MODE(ctypes.Structure):
    _fields_ = [('infoType', ctypes.c_uint32), ('id', ctypes.c_uint32), ('adapterId', _LUID),
                ('width', ctypes.c_uint32), ('height', ctypes.c_uint32), ('pixelFormat', ctypes.c_uint32),
                ('x', ctypes.c_int32), ('y', ctypes.c_int32), ('_pad', ctypes.c_ubyte * 28)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [('header', _HEADER), ('flags', ctypes.c_uint32), ('outputTechnology', ctypes.c_uint32),
                ('edidManufactureId', ctypes.c_uint16), ('edidProductCodeId', ctypes.c_uint16),
                ('connectorInstance', ctypes.c_uint32), ('friendly', wintypes.WCHAR * 64),
                ('path', wintypes.WCHAR * 128)]


def _query(flags):
    np_, nm = ctypes.c_uint32(), ctypes.c_uint32()
    for _ in range(5):  # la configurazione può cambiare tra le due chiamate: riprovo
        if user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(np_), ctypes.byref(nm)) != 0:
            return [], []
        paths, modes = (_FULLPATH * np_.value)(), (_MODE * nm.value)()
        r = user32.QueryDisplayConfig(flags, ctypes.byref(np_), paths, ctypes.byref(nm), modes, None)
        if r == 0:
            return list(paths[:np_.value]), list(modes[:nm.value])
        if r != 122:  # 122 = buffer troppo piccolo
            break
    return [], []


def _target_name(p):
    t = _TARGET_NAME()
    t.header.type, t.header.size = 2, ctypes.sizeof(t)  # 2 = nome del monitor
    t.header.adapterId, t.header.id = p.tgt.adapterId, p.tgt.id
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(t)) != 0:
        return None
    return t


def _gdi_name(p):
    sn = _SOURCE_NAME()
    sn.header.type, sn.header.size = 1, ctypes.sizeof(sn)
    sn.header.adapterId, sn.header.id = p.src.adapterId, p.src.id
    return sn.viewGdiDeviceName if user32.DisplayConfigGetDeviceInfo(ctypes.byref(sn)) == 0 else None


def _is_virtual_target(t):
    s = (t.path + ' ' + t.friendly).lower()
    return 'mtt1337' in s or 'mttvdd' in s


def virtual_slots():
    """Gli schermi virtuali creati dal driver, accesi o spenti:
    {numero connettore: {'active': bool, 'device': '\\.\DISPLAYn' oppure None, 'uid': percorso del monitor}}."""
    slots = {}
    paths, _ = _query(QDC_ALL_PATHS)
    for p in paths:
        if not p.tgt.targetAvailable:
            continue
        t = _target_name(p)
        if not t or not _is_virtual_target(t):
            continue
        slot = slots.setdefault(t.connectorInstance, {'active': False, 'device': None, 'uid': t.path})
        if p.flags & PATH_ACTIVE:
            slot['active'] = True
            slot['device'] = _gdi_name(p)
    return slots


def _apply(paths, modes):
    np_ = len(paths)
    pa = (_FULLPATH * np_)(*paths)
    ma = (_MODE * len(modes))(*modes) if modes else None
    return user32.SetDisplayConfig(np_, pa, len(modes), ma, SDC_APPLY | SDC_USE_SUPPLIED | SDC_SAVE | SDC_ALLOW_CHANGES)


def detach_slot(slot):
    """Spegne uno schermo virtuale (come scollegarlo). Restituisce il codice di Windows (0 = ok)."""
    paths, modes = _query(QDC_ONLY_ACTIVE)
    keep = []
    found = False
    for p in paths:
        t = _target_name(p)
        if t and _is_virtual_target(t) and t.connectorInstance == slot:
            found = True
            continue
        keep.append(p)
    if not found:
        return 0
    if not keep:
        return -1  # non spengo mai l'ultimo schermo acceso
    return _apply(keep, modes)


def slot_geometry(slot):
    """(x, y, larghezza, altezza) di uno schermo virtuale acceso, oppure None."""
    dev = virtual_slots().get(slot, {}).get('device')
    m = dev and by_device(dev)
    return (m['x'], m['y'], m['w'], m['h']) if m else None


def attach_slot(slot, geometry=None):
    """Riaccende uno schermo virtuale. geometry = (x, y, larghezza, altezza) da ripristinare;
    senza, Windows sceglie da solo (risoluzione ricordata, posizione a destra)."""
    active, modes = _query(QDC_ONLY_ACTIVE)
    for p in active:
        t = _target_name(p)
        if t and _is_virtual_target(t) and t.connectorInstance == slot:
            return 0  # già acceso
    used = {(p.src.adapterId.low, p.src.adapterId.high, p.src.id) for p in active}
    allp, _ = _query(QDC_ALL_PATHS)
    for p in allp:
        if not p.tgt.targetAvailable:
            continue
        t = _target_name(p)
        if not t or not _is_virtual_target(t) or t.connectorInstance != slot:
            continue
        if (p.src.adapterId.low, p.src.adapterId.high, p.src.id) in used:
            continue
        p.flags |= PATH_ACTIVE
        p.src.modeInfoIdx = MODE_IDX_INVALID
        p.tgt.modeInfoIdx = MODE_IDX_INVALID
        if geometry:
            x, y, w, h = geometry
            sm = _SOURCE_MODE(infoType=1, id=p.src.id, adapterId=p.src.adapterId,
                              width=w, height=h, pixelFormat=4, x=x, y=y)  # 4 = 32 bit
            p.src.modeInfoIdx = len(modes)
            modes = modes + [ctypes.cast(ctypes.pointer(sm), ctypes.POINTER(_MODE)).contents]
        r = _apply(active + [p], modes)
        if r != 0 and geometry:  # posizione non accettata: lascio scegliere a Windows
            p.src.modeInfoIdx = MODE_IDX_INVALID
            r = _apply(active + [p], modes[:-1])
        return r
    return -2  # schermo non trovato (il driver ne ha meno di quanti servono)


def set_slot_position(slot, x, y):
    """Sposta uno schermo virtuale acceso nella posizione indicata (rispetto al monitor principale)."""
    paths, modes = _query(QDC_ONLY_ACTIVE)
    for p in paths:
        t = _target_name(p)
        if not (t and _is_virtual_target(t) and t.connectorInstance == slot):
            continue
        i = p.src.modeInfoIdx
        if i == MODE_IDX_INVALID or i >= len(modes):
            return -3
        sm = _SOURCE_MODE.from_buffer_copy(bytes(modes[i]))
        if (sm.x, sm.y) == (x, y):
            return 0
        sm.x, sm.y = x, y
        modes[i] = _MODE.from_buffer_copy(bytes(sm))
        return _apply(paths, modes)
    return -2

if __name__ == '__main__':
    outs = dxgi_outputs()
    for m in list_monitors():
        print(m['device'], f"{m['w']}x{m['h']} @ {m['x']},{m['y']}", 'VIRTUALE' if is_virtual(m) else '',
              'cattura:', outs.get(m['device']), 'scala:', get_scale(m['device']))
    print('schermi virtuali:', virtual_slots())
