"""Commands to the Virtual Display Driver: number of virtual screens and available resolutions.

The driver reads C:\\VirtualDisplayDriver\\vdd_settings.xml and accepts commands on the pipe
\\\\.\\pipe\\MTTVirtualDisplayPipe (open to every user, no administrator rights needed).
After RELOAD_DRIVER all virtual screens turn off and on again for a moment.
"""
import ctypes
import os
import xml.etree.ElementTree as ET
from ctypes import wintypes

SETTINGS = r'C:\VirtualDisplayDriver\vdd_settings.xml'
PIPE = r'\\.\pipe\MTTVirtualDisplayPipe'

_k = ctypes.WinDLL('kernel32', use_last_error=True)
_k.CreateFileW.restype = wintypes.HANDLE
_k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID = wintypes.HANDLE(-1).value


def command(cmd):
    """Sends a command to the driver and returns the answer (text), or None if the driver doesn't answer."""
    h = _k.CreateFileW(PIPE, 0xC0000000, 0, None, 3, 0, None)  # read+write, open existing
    if not h or h == INVALID:
        return None
    try:
        data = cmd.encode('utf-16-le')
        n = wintypes.DWORD()
        if not _k.WriteFile(h, data, len(data), ctypes.byref(n), None):
            return None
        out = b''
        buf = ctypes.create_string_buffer(8192)
        while True:
            ok = _k.ReadFile(h, buf, len(buf), ctypes.byref(n), None)
            out += buf.raw[:n.value]
            if not ok and ctypes.get_last_error() != 234:  # 234 = more data coming
                break
            if ok and not n.value:
                break
        if len(out) > 1 and out[1:2] == b'\x00':
            return out.decode('utf-16-le', 'replace').rstrip('\x00')
        return out.decode('utf-8', 'replace')
    finally:
        _k.CloseHandle(h)


def available():
    return command('PING') == 'PONG'


def _load():
    return ET.parse(SETTINGS)


def read_settings():
    """(number of screens, [(width, height)])."""
    root = _load().getroot()
    count = int(root.findtext('monitors/count', '1'))
    res = [(int(r.findtext('width')), int(r.findtext('height'))) for r in root.iter('resolution')]
    return count, res


def write_settings(count=None, add=()):
    """Updates the driver file. Returns True if something changed (a RELOAD_DRIVER is needed)."""
    tree = _load()
    root = tree.getroot()
    changed = False
    if count is not None:
        node = root.find('monitors/count')
        if node is not None and node.text.strip() != str(count):
            node.text = str(count)
            changed = True
    res_root = root.find('resolutions')
    have = {(int(r.findtext('width')), int(r.findtext('height'))) for r in root.iter('resolution')}
    for w, h in add:
        if (w, h) in have:
            continue
        r = ET.SubElement(res_root, 'resolution')
        for tag, val in (('width', w), ('height', h), ('refresh_rate', 60)):
            ET.SubElement(r, tag).text = str(val)
        have.add((w, h))
        changed = True
    if changed:
        ET.indent(tree, '    ')
        tmp = SETTINGS + '.tmp'
        tree.write(tmp, encoding='utf-8', xml_declaration=True)
        os.replace(tmp, SETTINGS)
    return changed


def reload():
    return command('RELOAD_DRIVER')


if __name__ == '__main__':
    print('driver raggiungibile:', available())
    print('impostazioni:', read_settings())
