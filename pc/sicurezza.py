"""Shared protections: what can be opened on the PC, requests only from the PC, security headers."""
import os
import re

from aiohttp import web

# Types a device can have opened on the PC with "Open here": documents and media files.
# No programs, scripts, shortcuts or Office documents with macros: opening them would mean
# running code that came from a device.
SAFE_OPEN = {
    '.pdf', '.txt', '.md', '.csv', '.rtf', '.log', '.json', '.xml',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic', '.heif', '.tif', '.tiff',
    '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg',
    '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.epub', '.zip',
}


def safe_to_open(name):
    return os.path.splitext(name)[1].lower() in SAFE_OPEN


def mark_from_internet(path):
    """Marks the file as "downloaded from the Internet" (Mark of the Web): Windows SmartScreen and
    Office Protected View treat it with due caution."""
    try:
        with open(path + ':Zone.Identifier', 'w', encoding='ascii') as f:
            f.write('[ZoneTransfer]\r\nZoneId=3\r\n')
    except OSError:
        pass  # non-NTFS disk: no marking


def clean_text(value, limit=40):
    """Text coming from a device (e.g. the name): without control characters, so it can't
    break or fake console lines."""
    return re.sub(r'[\x00-\x1f\x7f  ]', ' ', str(value or '')).strip()[:limit]


def local_only(request, local_port):
    """Requests allowed only from the app window on this PC.
    - local source address;
    - exact Host (blocks sites pretending to be the PC with "DNS rebinding");
    - if the browser states the origin, it must be the app window (blocks web pages
      open on the PC that try to control the app or read the console)."""
    hosts = {f'127.0.0.1:{local_port}', f'localhost:{local_port}'}
    if request.remote not in ('127.0.0.1', '::1') or request.host not in hosts:
        raise web.HTTPForbidden(text='Solo dalla finestra dell\'app su questo PC.')
    origin = request.headers.get('Origin')
    if origin is not None and origin not in {'http://' + h for h in hosts}:
        raise web.HTTPForbidden(text='Origine non ammessa.')


@web.middleware
async def headers(request, handler):
    resp = await handler(request)
    h = resp.headers
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('Referrer-Policy', 'no-referrer')  # links contain the device key
    h.setdefault('X-Frame-Options', 'DENY')
    if request.path.startswith('/api/file/'):
        # files uploaded by devices: never run as an app page (no scripts, isolated origin)
        h['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self'; media-src 'self'"
    elif request.path in ('/', '/index.html'):
        host = request.host
        h.setdefault('Content-Security-Policy',
                     "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                     f"style-src 'self' 'unsafe-inline'; connect-src 'self' wss://{host}; "
                     "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    elif request.path == '/pc':
        h.setdefault('Content-Security-Policy',
                     "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                     f"connect-src 'self' ws://{request.host}; img-src 'self' data:; frame-ancestors 'none'")
    return resp
