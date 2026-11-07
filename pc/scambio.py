"""File exchange between the PC and the devices.

Everything goes through the "downloads" folder inside the app (data\\downloads).
Every file in the folder is visible to all devices, connected now or in the future.
A file can be added in three ways:
- by copying it into the folder with File Explorer (the folder is checked every 2 seconds);
- from the PC with right-click > Send to > Schermo Esteso (invia.py);
- from the app, with "Add files" or by dragging it into the Files panel.
Files disappear only when you delete them from the folder on the PC: devices can't delete them.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from urllib.parse import quote, unquote

from aiohttp import web

import riposo
import sicurezza

# IPAD_SCHERMO_CARTELLA is only for tests, so the real folder isn't touched
FOLDER = os.environ.get('IPAD_SCHERMO_CARTELLA') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'downloads')
META = os.path.join(os.path.dirname(FOLDER), 'download-info.json')  # who added what (outside the folder)
CHUNK = 1 << 20
MAX_FILE = 20 * 1024 ** 3     # 20 GB per file
MIN_FREE = 2 * 1024 ** 3      # always leave at least 2 GB free on the disk
SCAN = 2  # seconds between two folder checks


def _safe_name(name):
    name = str(name)
    if re.search(r'%[0-9A-Fa-f]{2}', name):  # some programs send the name encoded (%C3%A0 = à)
        name = unquote(name)
    name = os.path.basename(name.replace('\\', '/')).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?', name):
        name = '_' + name  # Windows reserved names
    return name[:150] or 'file'


def _unique(name):
    base, ext = os.path.splitext(name)
    out, n = name, 1
    while os.path.exists(os.path.join(FOLDER, out)):
        out = f'{base} ({n}){ext}'
        n += 1
    return out


def _size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def _visible(name):
    return not name.startswith('.') and not name.endswith('.parziale') and name.lower() != 'desktop.ini'


class Exchange:
    def __init__(self, app, log):
        self.app = app
        self.log = log
        os.makedirs(FOLDER, exist_ok=True)
        self.known = self._scan()  # name -> (size, date) at the last check

    # ---- folder ----
    def _scan(self):
        out = {}
        for e in os.scandir(FOLDER):
            if e.is_file() and _visible(e.name):
                st = e.stat()
                out[e.name] = (st.st_size, st.st_mtime)
        return out

    def _meta(self):
        try:
            with open(META, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _note(self, name, sender):
        meta = self._meta()
        meta[name] = {'da': sender}
        meta = {k: v for k, v in meta.items() if os.path.exists(os.path.join(FOLDER, k))}
        with open(META, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)

    def files(self):
        meta = self._meta()
        out = [{'nome': n, 'dim': _size(sz), 'ora': mt, 'da': meta.get(n, {}).get('da', 'PC')}
               for n, (sz, mt) in self._scan().items()]
        out.sort(key=lambda f: f['ora'], reverse=True)
        return out

    def broadcast(self, new=(), sender=None, skip=None):
        """Tells every connected device that the folder changed (and which files are new)."""
        msg = json.dumps({'t': 'file', 'nomi': list(new), 'da': sender})
        for s in self.app.sessions.values():
            if s.id == skip:
                continue
            for c in s.clients:
                c.push_text(msg)

    async def watch(self):
        """Notices files copied or deleted with File Explorer."""
        pending = {}  # new files still being copied: wait until the size stops changing
        while True:
            await riposo.nap(SCAN)
            try:
                now = await asyncio.to_thread(self._scan)
            except OSError:
                continue
            added = []
            for n, info in now.items():
                if n in self.known:
                    continue
                if pending.get(n) == info:
                    added.append(n)
                    pending.pop(n)
                else:
                    pending[n] = info
            removed = [n for n in self.known if n not in now]
            for n in added:
                self.known[n] = now[n]
            for n in removed:
                self.known.pop(n, None)
            pending = {n: i for n, i in pending.items() if n in now}
            if added:
                self.log(f'caricato file {", ".join(added)} (copiato nella cartella download)', 'PC')
            if removed:
                self.log(f'eliminato file {", ".join(removed)} dalla cartella download', 'PC')
            if added or removed:
                self.broadcast(added, 'PC' if added else None)

    def _added(self, names, sender, skip=None):
        """Files just saved by the server: mark them as known right away and tell the others."""
        now = self._scan()
        for n in names:
            self._note(n, sender)
            if n in now:
                self.known[n] = now[n]
        self.log(f'caricato file {", ".join(names)}', sender)
        self.broadcast(names, sender, skip)

    # ---- access ----
    def _auth(self, request):
        q = request.query
        dev = q.get('dev', '')
        if not self.app.devices.check(dev, q.get('token', '')):
            raise web.HTTPForbidden(text='dispositivo non abbinato')
        return dev

    def _local(self, request):
        sicurezza.local_only(request, self.app.local_port)

    # ---- routes ----
    async def list_handler(self, request):
        self._auth(request)
        return web.json_response({'file': self.files()})

    async def upload_handler(self, request):
        dev = self._auth(request)
        return await self._receive(request, self.app.devices.get(dev)['nome'], dev)

    async def pc_list_handler(self, request):
        """List for the PC control window."""
        self._local(request)
        return web.json_response({'file': self.files(), 'cartella': FOLDER})

    async def pc_upload_handler(self, request):
        """Files dragged into the PC control window."""
        self._local(request)
        return await self._receive(request, 'PC', None)

    async def _receive(self, request, sender, dev):
        reader = await request.multipart()
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != 'file' or not part.filename:
                continue
            name = _unique(_safe_name(part.filename))
            tmp = os.path.join(FOLDER, name + '.parziale')
            try:
                written = 0
                with open(tmp, 'wb') as f:
                    while True:
                        chunk = await part.read_chunk(CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        # a huge file (even by mistake) must not fill the PC disk
                        if written > MAX_FILE or shutil.disk_usage(FOLDER).free < MIN_FREE + len(chunk):
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE, actual_size=written,
                                                                text='spazio sul disco del PC quasi finito')
                        await asyncio.to_thread(f.write, chunk)
                os.replace(tmp, os.path.join(FOLDER, name))
                if dev:  # came from a device: Windows treats it as downloaded from the Internet
                    sicurezza.mark_from_internet(os.path.join(FOLDER, name))
            except BaseException:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            saved.append(name)
        if saved:
            self._added(saved, sender, skip=dev)
        return web.json_response({'salvati': saved})

    async def file_handler(self, request):
        dev = self._auth(request)
        name = _safe_name(request.match_info['nome'])
        path = os.path.join(FOLDER, name)
        if not _visible(name) or not os.path.isfile(path):
            raise web.HTTPNotFound(text='file non trovato')
        # PDF and video players ask for the file in pieces: log only the first request
        rng = request.headers.get('Range', '')
        if not rng or rng.startswith('bytes=0-'):
            what = 'scaricato' if request.query.get('scarica') else 'aperto'
            self.log(f'{what} file {name}', self.app.devices.get(dev)['nome'])
        resp = web.FileResponse(path)
        if request.query.get('scarica'):
            # name with special characters encoded as per RFC 5987
            resp.headers['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(name)}"
        return resp

    async def local_handler(self, request):
        """From the PC (invia.py): copies the files into the folder and notifies the connected devices."""
        self._local(request)
        d = await request.json()
        names = []
        for p in d.get('percorsi', []):
            if not os.path.isfile(p):
                continue
            if os.path.dirname(os.path.abspath(p)) == os.path.abspath(FOLDER):
                name = os.path.basename(p)  # already in the folder
            else:
                name = _unique(_safe_name(os.path.basename(p)))
                await asyncio.to_thread(shutil.copy2, p, os.path.join(FOLDER, name))
            names.append(name)
        if names:
            self._added(names, 'PC')
        online = sum(1 for s in self.app.sessions.values() if s.clients)
        return web.json_response({'inviati': names, 'dispositivi': online})

    def routes(self, router):
        router.add_get('/api/file', self.list_handler)
        router.add_post('/api/invia', self.upload_handler)
        router.add_get('/api/file/{nome}', self.file_handler)
        router.add_post('/api/locale', self.local_handler)
        router.add_get('/pc/file', self.pc_list_handler)
        router.add_post('/pc/invia', self.pc_upload_handler)

    def path_of(self, name):
        name = _safe_name(name)
        path = os.path.join(FOLDER, name)
        return path if _visible(name) and os.path.isfile(path) else None

    def open_file(self, name):
        """Opens a file from the folder with the Windows default program."""
        name = _safe_name(name)
        path = os.path.join(FOLDER, name)
        if not (_visible(name) and os.path.isfile(path)):
            return
        if sicurezza.safe_to_open(name):
            self.log(f'aperto file {name}', 'PC')
            os.startfile(path)
        else:
            # programs and scripts: not started, the file is shown selected in File Explorer
            self.log(f'{name} non viene aperto direttamente (potrebbe essere un programma): lo mostro nella cartella', 'PC')
            subprocess.Popen(['explorer.exe', '/select,', path])
