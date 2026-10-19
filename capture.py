"""Cattura dello schermo virtuale e compressione H.264 con FFmpeg.

FFmpeg cattura con Desktop Duplication (ddagrab, tutto in GPU), comprime con
Intel Quick Sync (h264_qsv) e scrive un flusso FLV su stdout. L'FLV indica la
lunghezza di ogni fotogramma in testa, così ogni fotogramma si inoltra appena
arriva, senza aspettare il successivo.

Se FFmpeg si blocca o si chiude (cambio risoluzione, schermata UAC, driver
riavviato...) viene riavviato da solo.
"""
import asyncio
import base64
import glob
import os
import re
import shutil
import time

NO_WINDOW = 0x08000000  # FFmpeg senza finestra nera quando l'app gira senza terminale
FRAME_TIMEOUT = 3.0  # secondi senza fotogrammi => FFmpeg è bloccato, lo riavvio

# Livelli di qualità: l'iPad scende di livello da solo se Safari va in crisi di memoria.
QUALITY = [
    {'nome': 'Massima', 'fps': 60, 'scala': 1.0, 'bitrate': '14M', 'maxrate': '20M'},
    {'nome': 'Fluida', 'fps': 30, 'scala': 1.0, 'bitrate': '10M', 'maxrate': '14M'},
    {'nome': 'Media', 'fps': 30, 'scala': 0.75, 'bitrate': '7M', 'maxrate': '10M'},
    {'nome': 'Leggera', 'fps': 30, 'scala': 0.5, 'bitrate': '4M', 'maxrate': '6M'},
]


def out_size(w, h, scala):
    # dimensioni pari (richiesto da H.264)
    return int(w * scala) // 2 * 2, int(h * scala) // 2 * 2


def find_ffmpeg():
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    pat = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe')
    found = sorted(glob.glob(pat))
    if found:
        return found[-1]
    raise RuntimeError('FFmpeg non trovato: installalo con "winget install Gyan.FFmpeg"')


FFMPEG = find_ffmpeg()


async def probe_outputs(max_outputs=6):
    """Dimensioni di ogni uscita catturabile da ddagrab: {indice: (larghezza, altezza)}."""
    found = {}
    for i in range(max_outputs):
        p = await asyncio.create_subprocess_exec(
            FFMPEG, '-hide_banner', '-f', 'lavfi', '-i', f'ddagrab=output_idx={i}:framerate=5',
            '-frames:v', '1', '-f', 'null', '-',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW)
        _, err = await p.communicate()
        m = re.search(rb'Video: .*?(\d{3,5})x(\d{3,5})', err)
        if not m:
            break
        found[i] = (int(m.group(1)), int(m.group(2)))
    return found


def encoder_args(kind, s):
    if kind == 'qsv':
        return ['-c:v', 'h264_qsv', '-preset', 'veryfast', '-profile:v', 'main',
                '-async_depth', '1', '-bf', '0', '-look_ahead', '0',
                '-g', str(s['gop']), '-b:v', s['bitrate'], '-maxrate', s['maxrate'], '-bufsize', s['bufsize']]
    return ['-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-profile:v', 'main',
            '-pix_fmt', 'yuv420p', '-bf', '0', '-g', str(s['gop']),
            '-b:v', s['bitrate'], '-maxrate', s['maxrate'], '-bufsize', s['bufsize']]


def ffmpeg_cmd(kind, output_idx, s, size=None):
    grab = f"ddagrab=output_idx={output_idx}:framerate={s['fps']}:draw_mouse=1"
    if kind == 'qsv':
        chain = grab + ',hwmap=derive_device=qsv,format=qsv'
        if size:
            chain += f',scale_qsv=w={size[0]}:h={size[1]}'
        pre = ['-init_hw_device', 'd3d11va', '-filter_complex', chain]
    else:
        chain = grab + ',hwdownload,format=bgra'
        if size:
            chain += f',scale={size[0]}:{size[1]}:flags=bilinear'
        pre = ['-filter_complex', chain]
    return [FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', *pre, *encoder_args(kind, s),
            '-f', 'flv', '-flvflags', 'no_duration_filesize', '-flush_packets', '1', 'pipe:1']


def codec_string(avcc):
    """Stringa codec WebCodecs (es. avc1.4d0028) dal record di configurazione AVC."""
    return 'avc1.%02x%02x%02x' % (avcc[1], avcc[2], avcc[3])


class Capture:
    """Avvia FFmpeg, legge i fotogrammi e li passa a on_config / on_frame."""

    def __init__(self, settings, get_target, on_config, on_frame, log):
        self.s = settings
        self.get_target = get_target  # -> (indice ddagrab, larghezza, altezza) oppure None
        self.on_config = on_config
        self.on_frame = on_frame
        self.log = log
        self.kind = 'qsv' if settings.get('encoder', 'auto') in ('auto', 'qsv') else 'x264'
        self.proc = None
        self.last_frame = 0.0
        self.running = False
        self.restarts = 0
        self.level = 0
        self.apply_level(settings.get('quality', 0))

    def apply_level(self, level):
        level = max(0, min(int(level), len(QUALITY) - 1))
        self.level = level
        self.s.update({k: QUALITY[level][k] for k in ('fps', 'bitrate', 'maxrate')})
        self.s['gop'] = QUALITY[level]['fps']  # un fotogramma completo al secondo
        return level

    async def run(self):
        self.running = True
        fails = 0
        missing = False
        while self.running:
            target = await self.get_target()
            if target is None:
                if not missing:
                    self.log('Schermo virtuale non ancora pronto: aspetto')
                missing = True
                await asyncio.sleep(1)
                continue
            missing = False
            idx, w, h = target
            started = time.monotonic()
            got = await self._run_once(idx, w, h)
            if not self.running:
                break
            if got:
                fails = 0
            else:
                fails += 1
                if fails >= 2 and self.kind == 'qsv' and self.s.get('encoder', 'auto') == 'auto':
                    self.log('Quick Sync non disponibile: passo alla compressione via CPU (x264)')
                    self.kind = 'x264'
            self.restarts += 1
            # pausa breve, più lunga se fallisce subito più volte di fila
            await asyncio.sleep(0.3 if time.monotonic() - started > 5 else min(0.5 * (fails + 1), 3))

    async def _run_once(self, idx, w, h):
        scala = QUALITY[self.level]['scala']
        size = out_size(w, h, scala) if scala != 1.0 else None
        if size:
            w, h = size
        cmd = ffmpeg_cmd(self.kind, idx, self.s, size)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW)
        self.log(f'Cattura avviata: uscita {idx}, {w}x{h} @ {self.s["fps"]} fps, codifica {self.kind}, '
                 f'qualità {QUALITY[self.level]["nome"]}')
        self.access_lost = False
        err_task = asyncio.create_task(self._drain_stderr(self.proc.stderr))
        dog = asyncio.create_task(self._watchdog())
        self.last_frame = time.monotonic()
        got_frames = False
        try:
            rd = self.proc.stdout
            head = await rd.readexactly(13)  # intestazione FLV (9) + PreviousTagSize0 (4)
            if head[:3] != b'FLV':
                raise ValueError('flusso FLV non valido')
            while True:
                th = await rd.readexactly(11)
                size = int.from_bytes(th[1:4], 'big')
                data = await rd.readexactly(size)
                await rd.readexactly(4)
                if th[0] != 9 or size < 5:
                    continue  # solo i tag video
                frame_type, codec = data[0] >> 4, data[0] & 0x0F
                if codec != 7:
                    continue
                if data[1] == 0:  # AVCDecoderConfigurationRecord
                    avcc = data[5:]
                    self.on_config({'t': 'config', 'codec': codec_string(avcc), 'w': w, 'h': h, 'fps': self.s['fps'],
                                    'desc': base64.b64encode(avcc).decode()})
                elif data[1] == 1:
                    self.last_frame = time.monotonic()
                    got_frames = True
                    self.on_frame(data[5:], frame_type == 1)
        except (asyncio.IncompleteReadError, ValueError, ConnectionError):
            pass
        finally:
            dog.cancel()
            if self.proc.returncode is None:
                self.proc.kill()
            await self.proc.wait()
            await err_task
            if not self.access_lost:  # nel caso "schermo ricreato" la riga chiara c'è già
                self.log(f'Cattura fermata (codice {self.proc.returncode})')
        return got_frames

    async def _watchdog(self):
        while True:
            await asyncio.sleep(0.5)
            if time.monotonic() - self.last_frame > FRAME_TIMEOUT:
                self.log('Nessun fotogramma da FFmpeg da 3 s: lo riavvio')
                self.proc.kill()
                return

    async def _drain_stderr(self, stream):
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode('utf-8', 'replace').strip()
            if not text or 'non monotonically' in text:
                continue
            if '887a0026' in text:  # DXGI_ERROR_ACCESS_LOST
                self.access_lost = True
                self.log('Windows ha ricreato lo schermo (app a schermo intero, finestra UAC, blocco schermo…): '
                         'riprendo la cattura')
                continue
            if self.access_lost and ('external library' in text or 'Terminating thread' in text):
                continue  # seguito tecnico dello stesso evento
            self.log('ffmpeg: ' + text)

    def restart(self):
        """Riavvia FFmpeg (per esempio dopo un cambio di monitor)."""
        if self.proc and self.proc.returncode is None:
            self.proc.kill()

    def stop(self):
        self.running = False
        self.restart()
