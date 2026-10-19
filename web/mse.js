'use strict';
// Modalità "Compatibile": invece di decodificare con WebCodecs, impacchetto i fotogrammi H.264
// in MP4 frammentato e li do al lettore video di Safari (Media Source Extensions).
// È il percorso che Safari usa per i video normali: decodifica hardware e memoria stabile.
// Il ritardo è un po' più alto, quindi tengo il video sempre "inseguito" verso la fine del buffer.

const MP4 = (() => {
  const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
  const u16 = (n) => [(n >>> 8) & 255, n & 255];
  const str = (s) => [...s].map((c) => c.charCodeAt(0));
  const cat = (parts) => {
    let len = 0;
    for (const p of parts) len += p.length;
    const out = new Uint8Array(len);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  };
  const box = (type, ...parts) => {
    const body = cat(parts.map((p) => (p instanceof Uint8Array ? p : new Uint8Array(p))));
    return cat([new Uint8Array([...u32(body.length + 8), ...str(type)]), body]);
  };
  const full = (type, version, flags, ...parts) => box(type, [version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255], ...parts);
  const MATRIX = [...u32(0x10000), ...u32(0), ...u32(0), ...u32(0), ...u32(0x10000), ...u32(0), ...u32(0), ...u32(0), ...u32(0x40000000)];
  const TS = 90000;

  function init(avcc, w, h) {
    const avc1 = box('avc1',
      [0, 0, 0, 0, 0, 0, ...u16(1), ...u16(0), ...u16(0), ...u32(0), ...u32(0), ...u32(0),
        ...u16(w), ...u16(h), ...u32(0x480000), ...u32(0x480000), ...u32(0), ...u16(1),
        ...new Array(32).fill(0), ...u16(0x18), ...u16(0xffff)],
      box('avcC', avcc));
    const stbl = box('stbl',
      full('stsd', 0, 0, u32(1), avc1),
      full('stts', 0, 0, u32(0)), full('stsc', 0, 0, u32(0)),
      full('stsz', 0, 0, u32(0), u32(0)), full('stco', 0, 0, u32(0)));
    const minf = box('minf',
      full('vmhd', 0, 1, [0, 0, 0, 0, 0, 0, 0, 0]),
      box('dinf', full('dref', 0, 0, u32(1), full('url ', 0, 1))),
      stbl);
    const mdia = box('mdia',
      full('mdhd', 0, 0, u32(0), u32(0), u32(TS), u32(0), u16(0x55c4), u16(0)),
      full('hdlr', 0, 0, u32(0), str('vide'), new Array(12).fill(0), str('Video\0')),
      minf);
    const trak = box('trak',
      full('tkhd', 0, 3, u32(0), u32(0), u32(1), u32(0), u32(0), new Array(8).fill(0),
        u16(0), u16(0), u16(0), u16(0), MATRIX, u32(w << 16), u32(h << 16)),
      mdia);
    const moov = box('moov',
      full('mvhd', 0, 0, u32(0), u32(0), u32(1000), u32(0), u32(0x10000), u16(0x100), new Array(10).fill(0),
        MATRIX, new Array(24).fill(0), u32(2)),
      trak,
      box('mvex', full('trex', 0, 0, u32(1), u32(1), u32(0), u32(0), u32(0))));
    return cat([box('ftyp', str('isom'), u32(0x200), str('isomiso2avc1mp41')), moov]);
  }

  function fragment(seq, time, duration, data, key) {
    const flags = key ? 0x02000000 : 0x01010000;
    const traf = (offset) => box('traf',
      full('tfhd', 0, 0x020000, u32(1)),
      full('tfdt', 1, 0, u32(Math.floor(time / 0x100000000)), u32(time >>> 0)),
      full('trun', 0, 0x000701, u32(1), u32(offset), u32(duration), u32(data.length), u32(flags)));
    // la dimensione del moof serve dentro il trun stesso: lo costruisco due volte
    const size = box('moof', full('mfhd', 0, 0, u32(seq)), traf(0)).length;
    const moof = box('moof', full('mfhd', 0, 0, u32(seq)), traf(size + 8));
    return cat([moof, box('mdat', data)]);
  }

  return { init, fragment, TS };
})();

class MseRenderer {
  constructor(stage, onReport) {
    this.stage = stage;
    this.onReport = onReport;
    this.video = document.createElement('video');
    Object.assign(this.video, { muted: true, playsInline: true, autoplay: true, disablePictureInPicture: true });
    this.video.setAttribute('playsinline', '');
    this.video.setAttribute('muted', '');
    stage.append(this.video);
    this.w = 4; this.h = 3;
    this.ms = null; this.sb = null; this.queue = [];
    this.seq = 1; this.time = 0; this.dur = 3000;
    this.waitingKey = true;
    this.frames = 0;
    this.timer = setInterval(() => this.chase(), 200);
  }

  get width() { return this.w; }
  get height() { return this.h; }
  get method() { return 'video'; }
  get queueSize() { return this.queue.length; }

  configure(cfg, avcc) {
    this.w = cfg.w; this.h = cfg.h;
    this.dur = Math.round(MP4.TS / (cfg.fps || 30));
    this.queue = []; this.seq = 1; this.time = 0; this.waitingKey = true;
    const type = `video/mp4; codecs="${cfg.codec}"`;
    const MS = window.ManagedMediaSource || window.MediaSource;
    if (!MS || !MS.isTypeSupported(type)) {
      this.onReport({ mse: 'non supportato', type });
      return false;
    }
    if (this.video.src) { URL.revokeObjectURL(this.video.src); }
    const ms = new MS();
    this.ms = ms; this.sb = null;
    this.video.disableRemotePlayback = true;
    this.video.src = URL.createObjectURL(ms);
    ms.addEventListener('sourceopen', () => {
      if (this.ms !== ms) return;
      const sb = ms.addSourceBuffer(type);
      sb.mode = 'segments';
      sb.addEventListener('updateend', () => this.pump());
      sb.addEventListener('error', () => this.onReport({ mse: 'errore sourcebuffer' }));
      this.sb = sb;
      const head = MP4.init(avcc, cfg.w, cfg.h);
      head.init = true;
      this.queue.unshift(head);
      this.pump();
    }, { once: true });
    return true;
  }

  push(data, key) {
    if (this.waitingKey && !key) return false;
    // il lettore non sta al passo: butto la coda e riparto dal prossimo fotogramma completo
    if (!key && this.queue.length > 20) { this.waitingKey = true; this.queue = this.queue.filter((q) => q.init); return false; }
    this.waitingKey = false;
    this.queue.push(MP4.fragment(this.seq++, this.time, this.dur, data, key));
    this.time += this.dur;
    this.frames++;
    this.pump();
    return true;
  }

  pump() {
    const sb = this.sb;
    if (!sb || sb.updating || !this.queue.length) return;
    // accodo più fotogrammi in un'unica append: meno lavoro per Safari
    const parts = this.queue.splice(0, 8);
    let len = 0;
    for (const p of parts) len += p.length;
    const buf = new Uint8Array(len);
    let o = 0;
    for (const p of parts) { buf.set(p, o); o += p.length; }
    try { sb.appendBuffer(buf); } catch (e) {
      // QuotaExceeded o stato sbagliato: libero il vecchio e riparto dal prossimo fotogramma completo
      this.onReport({ mse: 'append: ' + e.name });
      this.waitingKey = true;
      this.trim(true);
    }
  }

  chase() {
    const v = this.video, b = v.buffered;
    if (!b.length) return;
    const end = b.end(b.length - 1);
    const lag = end - v.currentTime;
    if (v.paused) v.play().catch(() => {});
    if (lag > 0.5 || v.currentTime < b.start(b.length - 1)) v.currentTime = Math.max(end - 0.05, b.start(b.length - 1));
    else v.playbackRate = lag > 0.12 ? 1.15 : lag > 0.06 ? 1.05 : 1.0;
    this.lag = lag;
    if (++this.tick % 25 === 0) this.trim(false);
  }

  trim(force) {
    // tengo solo gli ultimi secondi nel buffer, così la memoria resta costante
    const sb = this.sb, v = this.video;
    if (!sb || sb.updating || !v.buffered.length) return;
    const start = v.buffered.start(0);
    const cut = v.currentTime - (force ? 0.5 : 3);
    if (cut - start > 1) { try { sb.remove(start, cut); } catch {} }
  }

  destroy() {
    clearInterval(this.timer);
    try { if (this.ms && this.ms.readyState === 'open') this.ms.endOfStream(); } catch {}
    if (this.video.src) URL.revokeObjectURL(this.video.src);
    this.video.remove();
  }
}
MseRenderer.prototype.tick = 0;
