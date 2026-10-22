"""Protezioni comuni: cosa si può aprire sul PC, richieste solo dal PC, intestazioni di sicurezza."""
import os
import re

from aiohttp import web

# Tipi che un dispositivo può far aprire sul PC con "Apri qui": documenti e file multimediali.
# Niente programmi, script, collegamenti o documenti Office con macro: aprirli vorrebbe dire
# eseguire codice arrivato da un dispositivo.
SAFE_OPEN = {
    '.pdf', '.txt', '.md', '.csv', '.rtf', '.log', '.json', '.xml',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.heic', '.heif', '.tif', '.tiff',
    '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg',
    '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.epub', '.zip',
}


def safe_to_open(name):
    return os.path.splitext(name)[1].lower() in SAFE_OPEN


def mark_from_internet(path):
    """Segna il file come "scaricato da Internet" (Mark of the Web): Windows SmartScreen e la
    Visualizzazione protetta di Office lo trattano con le dovute cautele."""
    try:
        with open(path + ':Zone.Identifier', 'w', encoding='ascii') as f:
            f.write('[ZoneTransfer]\r\nZoneId=3\r\n')
    except OSError:
        pass  # disco non NTFS: niente marcatura


def clean_text(value, limit=40):
    """Testo arrivato da un dispositivo (es. il nome): senza caratteri di controllo, così non può
    spezzare o falsificare le righe della console."""
    return re.sub(r'[\x00-\x1f\x7f  ]', ' ', str(value or '')).strip()[:limit]


def local_only(request, local_port):
    """Richieste ammesse solo dalla finestra dell'app su questo PC.
    - indirizzo di provenienza locale;
    - Host esatto (blocca i siti che si fingono il PC con il "DNS rebinding");
    - se il browser indica l'origine, deve essere la finestra dell'app (blocca le pagine web
      aperte sul PC che provano a comandare l'app o a leggere la console)."""
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
    h.setdefault('Referrer-Policy', 'no-referrer')  # i link contengono la chiave del dispositivo
    h.setdefault('X-Frame-Options', 'DENY')
    if request.path.startswith('/api/file/'):
        # file caricati dai dispositivi: mai eseguiti come pagina dell'app (niente script, origine isolata)
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
