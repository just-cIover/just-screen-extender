'use strict';
// Schermo Esteso – client: riceve l'H.264 dal PC, lo decodifica in hardware
// (WebCodecs) e rimanda tocchi e mouse a Windows.

const $ = (id) => document.getElementById(id);
const stage = $('stage');
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* modalità privata */ } },
};

let ws = null;
let decoder = null;
let config = null;
let waitingKey = true;
let ts = 0;
let lastMsg = 0;
let retry = 0;
let retryTimer = null;
let offlineSince = 0;   // da quando il PC non risponde
let resting = false;    // il PC dorme: lo schermo del dispositivo può spegnersi
let mode = store.get('mode', 'touch');
let penMode = store.get('penMode2', 'auto'); // auto | penna | gomma | mouse
let pairing = false; // in attesa del PIN: niente controllo "nessun dato da 4 s"
let pairHold = 0;     // abbinamenti bloccati dal PC fino a quest'ora: niente tentativi a raffica

// Identità del dispositivo: un codice casuale creato la prima volta e la chiave che il PC
// consegna dopo il PIN. Con la chiave il PC lo riconosce e non chiede più il PIN.
let devId = store.get('devId', '');
if (!devId) {
  devId = (crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join(''));
  store.set('devId', devId);
}
const devName = /iPhone/.test(navigator.userAgent) ? 'iPhone'
  : (/iPad/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1)) ? 'iPad'
  : /Android/.test(navigator.userAgent) ? 'Android' : 'Browser';
// Area utile per lo schermo virtuale: tutto lo schermo, meno la barra in alto se l'opzione
// "Sotto la barra" è attiva. Solo nell'app aperta dalla Home (in Safari le barre cambiano) e in orizzontale.
function safeTop() {
  const probe = document.createElement('div');
  probe.style.cssText = 'position:fixed;top:0;height:env(safe-area-inset-top);visibility:hidden';
  document.body.append(probe);
  const v = probe.getBoundingClientRect().height;
  probe.remove();
  return v;
}
function sendArea() {
  const standalone = navigator.standalone || matchMedia('(display-mode: standalone), (display-mode: fullscreen)').matches;
  if (!standalone || innerWidth < innerHeight) return;
  if (document.activeElement && document.activeElement.tagName === 'INPUT') return; // tastiera aperta: niente cambi
  const dpr = window.devicePixelRatio || 1;
  const top = document.body.classList.contains('sottoBarra') ? safeTop() : 0;
  send({ t: 'area', w: Math.round(innerWidth * dpr), h: Math.round((innerHeight - top) * dpr) });
}
let areaTimer = null;
window.addEventListener('resize', () => { clearTimeout(areaTimer); areaTimer = setTimeout(sendArea, 800); });

function screenInfo() {
  const dpr = window.devicePixelRatio || 1;
  return { w: Math.round(screen.width * dpr), h: Math.round(screen.height * dpr), dpr };
}
let quality = +store.get('quality', '1');
const QUALITY_NAMES = ['Massima', 'Fluida', 'Media', 'Leggera'];

// Motori di visualizzazione:
//   diretto     -> WebCodecs + disegno YUV (ritardo minimo)
//   compatibile -> lettore video di Safari via MSE (stabilissimo, un po' più di ritardo)
const ua = navigator.userAgent;
const isSafari = /Safari/.test(ua) && !/Chrome|Chromium|CriOS|Edg/.test(ua);
const hasWC = 'VideoDecoder' in window;
const hasMSE = !!(window.ManagedMediaSource || window.MediaSource);
let engine = store.get('engine2', hasWC ? 'diretto' : 'compatibile');
if (engine === 'diretto' && !hasWC) engine = 'compatibile';

// Se la pagina era attiva fino a pochi secondi fa e non è stata chiusa normalmente,
// Safari l'ha fatta ripartire dopo un crash (memoria): passo al motore compatibile,
// e se crasha anche quello abbasso la qualità.
let crashed = null;
(() => {
  const alive = +store.get('alive', '0');
  if (alive && Date.now() - alive < 20000) {
    if (engine === 'diretto' && hasMSE) { engine = 'compatibile'; crashed = 'motore compatibile'; }
    else if (quality < QUALITY_NAMES.length - 1) { quality++; crashed = 'qualità ' + QUALITY_NAMES[quality]; }
    store.set('engine2', engine); store.set('quality', String(quality));
  }
  store.set('alive', '0');
})();
setInterval(() => { if (ws && ws.readyState === 1 && !document.hidden) store.set('alive', String(Date.now())); }, 1000);
window.addEventListener('pagehide', () => store.set('alive', '0'));
let statTick = 0;
const stat = { frames: 0, bytes: 0, rtt: null, drop: 0, fps: 0, rate: 0 };

const report = (r) => send({ t: 'diag', motore: engine, ...r, ua });
// Su Safari il disegno diretto dei VideoFrame perde memoria a ogni fotogramma: uso sempre il percorso YUV
const renderer = engine === 'compatibile'
  ? new MseRenderer(stage, report)
  : new Renderer(stage, report, isSafari ? 'yuv' : null);

/* ---------------- connessione ---------------- */

function setStatus(text) {
  $('status').classList.toggle('hidden', !text);
  if (text) $('statusText').textContent = text;
}

function connect() {
  clearTimeout(retryTimer);
  if (ws) { ws.onclose = null; try { ws.close(); } catch {} ws = null; }
  if (!hasWC && !hasMSE) {
    setStatus('Questo browser non supporta né WebCodecs né Media Source: aggiorna Safari.');
    return;
  }
  if (!resting) setStatus(retry ? 'Riconnessione…' : 'Connessione al PC…');  // PC addormentato: resta il suo messaggio
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const token = store.get('token', '');
  ws = new WebSocket(`${proto}://${location.host}/ws?dev=${encodeURIComponent(devId)}&token=${encodeURIComponent(token)}`);
  ws.binaryType = 'arraybuffer';
  lastMsg = performance.now();
  ws.onopen = () => { retry = 0; lastMsg = performance.now(); };
  ws.onmessage = onMessage;
  ws.onclose = (e) => {
    ws = null;
    if (pairing && Date.now() < pairHold) {
      // il PC ha bloccato gli abbinamenti: lascio il messaggio visibile e riprovo più tardi
      pairing = false;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, Math.min(pairHold - Date.now(), 60000));
      return;
    }
    if (pairing) { pairing = false; $('pinBox').classList.remove('show'); }
    scheduleRetry();
  };
  ws.onerror = () => { /* segue onclose */ };
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  if (document.hidden) return;
  retry++;
  if (!offlineSince) offlineSince = Date.now();
  if (Date.now() - offlineSince > 60000) rest(true);  // un minuto senza PC: forse dorme
  setStatus(resting ? 'Il PC è in sospensione: riprovo a collegarmi ogni 10 secondi.' : 'Connessione persa, riprovo…');
  // dopo un minuto senza PC si riprova di rado, per non consumare batteria
  retryTimer = setTimeout(connect, resting ? 10000 : Math.min(300 * retry, 2000));
}

/** Il PC dorme: niente schermo sempre acceso, il dispositivo si blocca da solo; al risveglio torna acceso. */
function rest(r) {
  if (r === resting) return;
  resting = r;
  if (r) {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    $('nosleep').pause();
    setStatus('Il PC è in sospensione. Lo schermo riparte da solo quando si sveglia.');
  } else {
    keepAwake();
  }
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function onMessage(ev) {
  lastMsg = performance.now();
  if (typeof ev.data === 'string') {
    const m = JSON.parse(ev.data);
    if (m.t === 'config') configure(m);
    else if (m.t === 'penna') {
      // il PC ha deciso: sotto la punta non c'è un programma da disegno, quindi scorre e tocca
      penFinger = m.uso === 'dito';
      if (penFinger) { strokes.length = 0; inkLast = null; ictx.clearRect(0, 0, ink.width, ink.height); }
    }
    else if (m.t === 'quality') {
      // decisa dal PC (o confermata): aggiorno il menu senza rimandarla indietro
      quality = m.level; store.set('quality', String(quality));
      if (m.auto) notice(`Qualità ${QUALITY_NAMES[quality]}: impostata in automatico dal PC`, null, null, 5000);
      document.querySelectorAll('#quality button').forEach((b) => b.setAttribute('aria-pressed', +b.dataset.q === quality));
    }
    else if (m.t === 'hello') {
      // riconosciuto dal PC
      offlineSince = 0; rest(false);
      pairing = false; $('pinBox').classList.remove('show');
      // la qualità la ricorda il PC (arriva subito dopo); la mando solo se l'ho abbassata io dopo un crash
      if (crashed) send({ t: 'quality', level: quality });
      report({ avvio: true, qualita: QUALITY_NAMES[quality], dopoCrash: crashed, schermo: screenInfo() });
      sendArea();
      crashed = null;
    } else if (m.t === 'pair') {
      pairing = true;
      send({ t: 'hello', nome: devName });
      showPin('');
    } else if (m.t === 'paired') {
      store.set('token', m.token);
      $('pinErr').textContent = '';
    } else if (m.t === 'pinerr') {
      if (m.bloccato || m.occupato) pairHold = Date.now() + (m.bloccato ? m.bloccato * 1000 : 30000);
      showPin(m.bloccato ? `Troppi tentativi sbagliati: riprova tra ${Math.ceil(m.bloccato / 60)} minuti.`
        : m.occupato ? 'Ci sono troppe richieste di abbinamento in corso: riprova tra qualche minuto.'
        : m.nuovo ? 'Il PIN è cambiato: inserisci quello nuovo che vedi sul PC.' : 'PIN errato, riprova.');
    } else if (m.t === 'avviso') {
      notice(m.testo, null, null, 5000);
    } else if (m.t === 'dove') {
      // spinta del mouse con più dispositivi collegati: "Qui" su quello che si trova da quella parte
      notice(`Hai spinto il mouse ${m.lato}: lo schermo va ${m.lato} del PC?`, 'Qui', () => send({ t: 'qui' }), (m.secondi || 15) * 1000);
    } else if (m.t === 'dove_fine') {
      $('toast').classList.remove('show');
    } else if (m.t === 'riposo') {
      rest(true);
    } else if (m.t === 'sveglio') {
      rest(false); setStatus('');
    } else if (m.t === 'file') {
      showToast(m);
    } else if (m.t === 'appunti') {
      clipFromPC(m);
    } else if (m.t === 'full') {
      pairing = false; $('pinBox').classList.remove('show');
      setStatus(`Il PC ha già ${m.max} dispositivi abbinati: rimuovine uno dal PC.`);
    }
    else if (m.t === 'pong') {
      stat.rtt = performance.now() - m.ts;
      stat.drop = m.drop;
    }
    return;
  }
  const buf = new Uint8Array(ev.data);
  stat.bytes += buf.byteLength;
  const key = buf[0] === 1;
  if (engine === 'compatibile') {
    if (renderer.push(buf.subarray(1), key)) {
      stat.frames++;
      if (!$('status').classList.contains('hidden')) setStatus('');
    }
    return;
  }
  if (!decoder || decoder.state !== 'configured') return;
  if (waitingKey && !key) return;
  // l'iPad non sta al passo: invece di accumulare fotogrammi in memoria, salto al prossimo completo
  if (!key && decoder.decodeQueueSize > 3) { waitingKey = true; stat.skips = (stat.skips || 0) + 1; return; }
  waitingKey = false;
  try {
    decoder.decode(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp: (ts += 16667), data: buf.subarray(1) }));
  } catch (e) {
    recover();
  }
}

/* ---------------- decodifica ---------------- */

function b64(s) {
  const bin = atob(s); const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function configure(m) {
  config = m;
  $('sRes').textContent = `${m.w}×${m.h}`;
  if (engine === 'compatibile') {
    renderer.configure(m, b64(m.desc));
    return;
  }
  if (decoder && decoder.state !== 'closed') { try { decoder.close(); } catch {} }
  decoder = new VideoDecoder({ output: draw, error: () => recover() });
  decoder.configure({
    codec: m.codec, description: b64(m.desc), codedWidth: m.w, codedHeight: m.h,
    optimizeForLatency: true, hardwareAcceleration: 'prefer-hardware',
  });
  waitingKey = true;
}

let recovering = false;
function recover() {
  // decoder in errore (succede se Safari lo sospende): ne creo uno nuovo e chiedo un fotogramma completo
  if (recovering || !config) return;
  recovering = true;
  setTimeout(() => {
    recovering = false;
    configure(config);
    send({ t: 'key' });
  }, 50);
}

let decoded = 0;
async function draw(frame) {
  decoded++;
  if (await renderer.draw(frame)) {
    stat.frames++;
    if (!$('status').classList.contains('hidden')) setStatus('');
  }
}

/* ---------------- controllo salute ---------------- */

setInterval(() => {
  if (!ws) return;
  if (ws.readyState === 1) send({ t: 'ping', ts: performance.now() });
  // nessun dato da 4 s (Wi-Fi addormentato, PC in pausa...): chiudo e mi ricollego subito
  if (!pairing && performance.now() - lastMsg > 4000) {
    ws.onclose = null; try { ws.close(); } catch {} ws = null;
    scheduleRetry();
  }
}, 1000);

setInterval(() => {
  stat.fps = stat.frames; stat.rate = stat.bytes * 8 / 1e6;
  stat.decoded = decoded;
  stat.frames = 0; stat.bytes = 0; decoded = 0;
  // ogni 10 s un riassunto al PC (compare nella finestra del server)
  // nei primi 20 s ogni 2 s (per capire subito se qualcosa va storto), poi ogni 10 s
  statTick++;
  if (statTick % 2 === 0 && ws && ws.readyState === 1) { // ogni 2 s, per la finestra del PC
    send({ t: 'stats', fps: stat.fps, dec: stat.decoded, rtt: stat.rtt && Math.round(stat.rtt),
      mbit: +stat.rate.toFixed(1), render: engine + '/' + renderer.method,
      queue: engine === 'compatibile' ? renderer.queueSize : decoder ? decoder.decodeQueueSize : null,
      lag: renderer.lag != null ? Math.round(renderer.lag * 1000) : undefined,
      skips: stat.skips || 0, q: QUALITY_NAMES[quality] });
  }
  if ($('menu').classList.contains('show')) {
    $('sFps').textContent = `${stat.fps}/s`;
    $('sRtt').textContent = stat.rtt == null ? '–' : `${Math.round(stat.rtt)} ms`;
    $('sRate').textContent = `${stat.rate.toFixed(1)} Mbit/s`;
    $('sDrop').textContent = stat.drop;
  }
  const dot = $('dot');
  dot.className = stat.rtt > 120 ? 'bad' : stat.rtt > 50 ? 'warn' : '';
}, 1000);

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    store.set('alive', '0');
    // in background Safari sospende tutto: chiudo pulito e riparto al ritorno
    clearTimeout(retryTimer);
    if (ws) { ws.onclose = null; try { ws.close(); } catch {} ws = null; }
  } else {
    retry = 0; offlineSince = 0; resting = false; connect(); keepAwake();
  }
});
window.addEventListener('pageshow', (e) => { if (e.persisted) connect(); });
window.addEventListener('online', () => { retry = 0; connect(); });

/* ---------------- schermo sempre acceso ---------------- */

let wakeLock = null;
async function keepAwake() {
  try {
    if ('wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch { /* non concesso: resta il video muto */ }
  const v = $('nosleep');
  if (v.paused) v.play().catch(() => {});
}

/* ---------------- tocco e mouse ---------------- */

function norm(e) {
  // posizione nel video (tenendo conto delle eventuali bande nere)
  const r = stage.getBoundingClientRect();
  const cw = renderer.width, ch = renderer.height;
  const s = Math.min(r.width / cw, r.height / ch);
  const w = cw * s, h = ch * s;
  const ox = r.left + (r.width - w) / 2, oy = r.top + (r.height - h) / 2;
  return { x: (e.clientX - ox) / w, y: (e.clientY - oy) / h };
}

const pointers = new Map(); // pointerId -> {x, y, sx, sy, t}
let gesture = null;         // modalità mouse: null | 'drag' | 'two'
let twoStart = 0, twoMoved = false, lastMid = null;

function mid() {
  let x = 0, y = 0;
  for (const p of pointers.values()) { x += p.cx; y += p.cy; }
  return { x: x / pointers.size, y: y / pointers.size };
}

/* ---------------- Apple Pencil ---------------- */
// La Pencil diventa una penna di Windows: pressione e inclinazione arrivano ai programmi
// (firma nei PDF di Edge, disegno, appunti, scrittura a mano). Mentre la Pencil scrive, i tocchi
// del palmo appoggiato vengono ignorati.
let penId = null, penUpAt = 0;
let penFinger = false;
let penTap = null; // inizio dell'ultimo appoggio della Pencil (per il doppio tocco) // in automatica: in questo tratto la Pencil fa da dito (niente inchiostro)

// Inchiostro immediato: il tratto compare subito sull'iPad e sfuma quando arriva quello vero dal PC
// (rete + compressione + decodifica). Così la scrittura sembra senza ritardo.
let inkOn = store.get('ink', '1') === '1';
const ink = $('ink');
const ictx = ink.getContext('2d');
const strokes = []; // segmenti {x0, y0, x1, y1, w, t}
let inkLast = null, inkRaf = 0;
function sizeInk() {
  const r = ink.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  ink.width = Math.round(r.width * d); ink.height = Math.round(r.height * d);
  ictx.setTransform(d, 0, 0, d, 0, 0);
}
window.addEventListener('resize', sizeInk);
sizeInk();
const inkLife = () => Math.min(600, Math.max(150, (stat.rtt || 60) + 120)); // quanto resta visibile
function inkPoint(e, start) {
  if (!inkOn || penFinger || (penMode !== 'penna' && penMode !== 'auto')) return;
  const r = ink.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  if (!start && inkLast) strokes.push({ x0: inkLast.x, y0: inkLast.y, x1: x, y1: y, w: 0.8 + (e.pressure || 0.5) * 2.4, t: performance.now() });
  inkLast = { x, y };
  if (!inkRaf) inkRaf = requestAnimationFrame(drawInk);
}
function drawInk() {
  inkRaf = 0;
  const now = performance.now(), life = inkLife();
  while (strokes.length && now - strokes[0].t > life) strokes.shift();
  ictx.clearRect(0, 0, ink.width, ink.height);
  ictx.lineCap = 'round';
  ictx.strokeStyle = '#4a4f5a';
  for (const s of strokes) {
    ictx.globalAlpha = Math.min(1, 2 * (1 - (now - s.t) / life)) * 0.9;
    ictx.lineWidth = s.w;
    ictx.beginPath(); ictx.moveTo(s.x0, s.y0); ictx.lineTo(s.x1, s.y1); ictx.stroke();
  }
  ictx.globalAlpha = 1;
  if (strokes.length) inkRaf = requestAnimationFrame(drawInk);
}
const penBusy = () => penId !== null || performance.now() - penUpAt < 600;
function sendPen(kind, e) {
  const n = norm(e);
  send({ t: 'pen', e: kind, x: n.x, y: n.y, p: kind === 'u' ? 0 : (e.pressure || 0.5),
    tx: e.tiltX || 0, ty: e.tiltY || 0, er: penMode === 'gomma', auto: penMode === 'auto' });
}
function penDown(e) {
  keepAwake(); closeMenu();
  try { stage.setPointerCapture(e.pointerId); } catch { /* tocco già terminato */ }
  if (pointers.size) cancelAll(); // il palmo era già appoggiato: annullo quei tocchi
  penId = e.pointerId;
  penFinger = false;
  penTap = { t0: performance.now(), x0: e.clientX, y0: e.clientY };
  sendPen('d', e);
  inkPoint(e, true);
}
function penMove(e) {
  if (e.pointerId !== penId) return;
  // la Pencil misura più punti di quelli che arrivano: li mando tutti, la linea viene più fluida
  const list = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
  for (const c of (list.length ? list : [e])) { sendPen('m', c); inkPoint(c); }
}
function penUp(e) {
  if (e.pointerId !== penId) return;
  sendPen(e.type === 'pointercancel' ? 'c' : 'u', e);
  if (penFinger && e.type !== 'pointercancel' && penTap) tapUp(penTap.t0, penTap.x0, penTap.y0, e);
  penId = null; penUpAt = performance.now();
  inkLast = null;
}
const isPen = (e) => e.pointerType === 'pen' && penMode !== 'mouse';

function onDown(e) {
  e.preventDefault();
  if (isPen(e)) { penDown(e); return; }
  if (e.pointerType === 'touch' && penBusy()) return;
  keepAwake();
  closeMenu();
  try { stage.setPointerCapture(e.pointerId); } catch { /* tocco già terminato */ }
  const n = norm(e);
  pointers.set(e.pointerId, { ...n, cx: e.clientX, cy: e.clientY, t0: performance.now(), x0: e.clientX, y0: e.clientY });
  if (pointers.size === 3) { cancelAll(); openMenu(); return; }

  if (e.pointerType === 'pen' || mode === 'mouse') {
    if (pointers.size === 1) {
      gesture = 'drag';
      send({ t: 'm', e: 'd', b: 0, x: n.x, y: n.y });
    } else if (pointers.size === 2) {
      // secondo dito: annullo il trascinamento e passo a scorrimento / clic destro
      if (gesture === 'drag') { const f = [...pointers.values()][0]; send({ t: 'm', e: 'u', b: 0, x: f.x, y: f.y }); }
      gesture = 'two'; twoStart = performance.now(); twoMoved = false; lastMid = mid();
    }
  } else {
    send({ t: 'p', e: 'd', id: e.pointerId, x: n.x, y: n.y });
  }
}

function onMove(e) {
  if (isPen(e)) { e.preventDefault(); penMove(e); return; }
  const p = pointers.get(e.pointerId);
  if (!p) return;
  e.preventDefault();
  const n = norm(e);
  Object.assign(p, n, { cx: e.clientX, cy: e.clientY });
  if (e.pointerType === 'pen' || mode === 'mouse') {
    if (gesture === 'drag') send({ t: 'm', e: 'm', b: 0, x: n.x, y: n.y });
    else if (gesture === 'two') {
      const m = mid();
      const dx = m.x - lastMid.x, dy = m.y - lastMid.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) {
        twoMoved = true;
        const r = norm({ clientX: m.x, clientY: m.y });
        send({ t: 'w', x: r.x, y: r.y, dx: Math.round(dx * 4), dy: Math.round(dy * 4) });
        lastMid = m;
      }
    }
  } else {
    send({ t: 'p', e: 'm', id: e.pointerId, x: n.x, y: n.y });
  }
}

function onUp(e) {
  if (isPen(e) || e.pointerId === penId) { e.preventDefault(); penUp(e); return; }
  const p = pointers.get(e.pointerId);
  if (!p) return;
  e.preventDefault();
  const n = norm(e);
  pointers.delete(e.pointerId);
  if (e.pointerType === 'pen' || mode === 'mouse') {
    if (gesture === 'drag') { send({ t: 'm', e: 'u', b: 0, x: n.x, y: n.y }); gesture = null; }
    else if (gesture === 'two' && pointers.size <= 1) {
      if (!twoMoved && performance.now() - twoStart < 350) send({ t: 'm', e: 'click', b: 2, x: n.x, y: n.y });
      gesture = pointers.size ? 'wait' : null;
    } else if (!pointers.size) gesture = null;
  } else {
    send({ t: 'p', e: e.type === 'pointercancel' ? 'c' : 'u', id: e.pointerId, x: n.x, y: n.y });
    if (e.type !== 'pointercancel' && !pointers.size) tapUp(p.t0, p.x0, p.y0, e);
  }
}

// Doppio tocco (dito, o Pencil dove fa da dito): il PC seleziona la parola con un doppio clic
let lastTap = null;
function tapUp(t0, x0, y0, e) {
  const now = performance.now();
  if (now - t0 > 350 || Math.hypot(e.clientX - x0, e.clientY - y0) > 12) { lastTap = null; return; }
  if (lastTap && now - lastTap.t < 450 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 30) {
    lastTap = null;
    const n = norm(e);
    send({ t: 'parola', x: n.x, y: n.y, gia: true }); // i due tocchi sono già arrivati a Windows
    notice('Parola selezionata sul PC', 'Copia', () => send({ t: 'tasto', k: 'copia' }), 6000);
  } else {
    lastTap = { t: now, x: e.clientX, y: e.clientY };
  }
}

function cancelAll() {
  for (const [id, p] of pointers) {
    if (mode === 'touch') send({ t: 'p', e: 'c', id, x: p.x, y: p.y });
  }
  if (gesture === 'drag') { const f = [...pointers.values()][0]; send({ t: 'm', e: 'u', b: 0, x: f.x, y: f.y }); }
  pointers.clear(); gesture = null;
}

stage.addEventListener('pointerdown', onDown);
stage.addEventListener('pointermove', onMove);
stage.addEventListener('pointerup', onUp);
stage.addEventListener('pointercancel', onUp);
// niente zoom, menu contestuale o selezione di Safari sopra lo schermo
for (const t of ['gesturestart', 'gesturechange', 'contextmenu', 'touchstart', 'touchmove'])
  stage.addEventListener(t, (e) => e.preventDefault(), { passive: false });

/* ---------------- menu ---------------- */

const HINTS = {
  touch: 'Le dita funzionano come su uno schermo touch di Windows. Tre dita insieme aprono questo menu.',
  mouse: 'Un dito muove e trascina, tocco con due dita = clic destro, due dita che scorrono = rotellina.',
};
function setQuality(q) {
  quality = q; store.set('quality', String(q));
  document.querySelectorAll('#quality button').forEach((b) => b.setAttribute('aria-pressed', +b.dataset.q === q));
  send({ t: 'quality', level: q });
}
document.querySelectorAll('#quality button').forEach((b) => b.addEventListener('click', () => setQuality(+b.dataset.q)));

document.querySelectorAll('#engine button').forEach((b) => {
  b.setAttribute('aria-pressed', b.dataset.e === engine);
  b.addEventListener('click', () => {
    if (b.dataset.e === engine) return;
    store.set('engine2', b.dataset.e);
    store.set('alive', '0');
    location.reload();
  });
});

const PEN_HINTS = {
  auto: 'Nei programmi da disegno (Paint, OneNote, Lavagna, Word, PDF…) la Pencil scrive; altrove fa da dito: trascinando scorri le pagine, toccando fai clic.',
  penna: 'La Pencil è una penna di Windows con pressione: per firmare un PDF aprilo in Edge e usa "Disegna".',
  gomma: 'La Pencil cancella, nei programmi che supportano la gomma della penna (Edge, OneNote, Paint…).',
  mouse: 'La Pencil fa da mouse preciso, come prima.',
};
function setOption(id, key, on, apply) {
  store.set(key, on ? '1' : '0');
  $(id).setAttribute('aria-pressed', on);
  apply(on);
}
const applyBar = (on) => { document.body.classList.toggle('sottoBarra', on); sizeInk(); sendArea(); };
$('optInk').addEventListener('click', () => setOption('optInk', 'ink', !inkOn, (on) => { inkOn = on; }));
$('optBar').addEventListener('click', () => setOption('optBar', 'sottoBarra', !document.body.classList.contains('sottoBarra'), applyBar));
setOption('optInk', 'ink', inkOn, () => {});
setOption('optBar', 'sottoBarra', store.get('sottoBarra', '1') === '1', applyBar);

function setPenMode(m) {
  penMode = m; store.set('penMode2', m);
  document.querySelectorAll('#penMode button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.p === m));
  $('penHint').textContent = PEN_HINTS[m];
}
document.querySelectorAll('#penMode button').forEach((b) => b.addEventListener('click', () => setPenMode(b.dataset.p)));

function setMode(m) {
  mode = m; store.set('mode', m);
  $('modeTouch').setAttribute('aria-pressed', m === 'touch');
  $('modeMouse').setAttribute('aria-pressed', m === 'mouse');
  $('modeHint').textContent = HINTS[m];
}
function openMenu() {
  $('menu').classList.add('show'); $('menuBtn').setAttribute('aria-expanded', 'true');
  document.body.classList.remove('idle');
}
function closeMenu() {
  $('menu').classList.remove('show'); $('menuBtn').setAttribute('aria-expanded', 'false');
  document.body.classList.add('idle');
}
$('menuBtn').addEventListener('click', () => ($('menu').classList.contains('show') ? closeMenu() : openMenu()));
$('modeTouch').addEventListener('click', () => setMode('touch'));
$('modeMouse').addEventListener('click', () => setMode('mouse'));
$('reconnect').addEventListener('click', () => { closeMenu(); retry = 0; connect(); });
$('changePin').addEventListener('click', () => {
  // dimentica la chiave: al prossimo collegamento il PC chiederà un PIN nuovo
  closeMenu(); store.set('token', ''); retry = 0; connect();
});

/* ---------------- PIN ---------------- */

function showPin(err) {
  setStatus('');
  $('pinErr').textContent = err;
  $('pinBox').classList.add('show');
  $('pin').value = '';
  setTimeout(() => $('pin').focus(), 50);
}
$('pinForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const v = $('pin').value.trim();
  if (!/^\d{6}$/.test(v)) { $('pinErr').textContent = 'Il PIN è di 6 cifre.'; return; }
  keepAwake();
  if (!ws || ws.readyState !== 1) { $('pinErr').textContent = 'Collegamento al PC perso, riprovo…'; retry = 0; connect(); return; }
  $('pinErr').textContent = '';
  send({ t: 'pin', pin: v, nome: devName, ...screenInfo() });
});

/* ---------------- file ---------------- */

const api = (path, extra = '') =>
  `${path}?dev=${encodeURIComponent(devId)}&token=${encodeURIComponent(store.get('token', ''))}${extra}`;
const fileUrl = (name, download) => api('/api/file/' + encodeURIComponent(name), download ? '&scarica=1' : '');

function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  for (const k of kids) e.append(k);
  return e;
}

function when(t) {
  const d = new Date(t * 1000), now = new Date();
  const hm = d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === now.toDateString() ? 'oggi ' + hm : d.toLocaleDateString('it-IT') + ' ' + hm;
}

async function refreshFiles() {
  let data;
  try {
    const r = await fetch(api('/api/file'), { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
  } catch {
    $('fileList').replaceChildren(el('li', { className: 'empty', textContent: 'Impossibile leggere i file dal PC.' }));
    return;
  }
  if (!data.file.length) {
    $('fileList').replaceChildren(el('li', { className: 'empty', textContent: 'Nessun file. Sul PC copiali nella cartella download dell’app, oppure clic destro > Invia a > Schermo Esteso.' }));
    return;
  }
  $('fileList').replaceChildren(...data.file.map((f) => {
    const info = el('div', { className: 'info', title: 'Anteprima' },
      el('div', { className: 'name', textContent: f.nome }),
      el('div', { className: 'meta', textContent: `${f.dim} · da ${f.da} · ${when(f.ora)}` }));
    info.addEventListener('click', () => preview(f.nome));
    return el('li', {}, info, shareButton(f.nome), openHere(f.nome));
  }));
}

/* Condividi (menu nativo di iPadOS) e anteprima interna.
   Niente link che aprono il file: in un'app sulla schermata Home non si tornerebbe più indietro. */
const files = new Map(); // nome -> File già scaricato (gli ultimi 3)
async function getFile(name) {
  if (files.has(name)) return files.get(name);
  const r = await fetch(fileUrl(name), { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const b = await r.blob();
  const f = new File([b], name, { type: b.type || 'application/octet-stream' });
  files.set(name, f);
  while (files.size > 3) files.delete(files.keys().next().value);
  return f;
}
function fileMsg(text) {
  $('fileMsg').textContent = text;
  clearTimeout(fileMsg.t);
  if (text) fileMsg.t = setTimeout(() => ($('fileMsg').textContent = ''), 6000);
}
async function shareFile(name, btn) {
  if (!navigator.share) {
    fileMsg('Per condividere serve la connessione sicura: vedi «Connessione sicura» in fondo al pannello.');
    return;
  }
  btn?.classList.add('busy');
  try {
    const f = await getFile(name);
    if (navigator.canShare && !navigator.canShare({ files: [f] })) { fileMsg('Questo tipo di file non si può condividere.'); return; }
    await navigator.share({ files: [f], title: name });
    btn?.classList.remove('ready');
  } catch (e) {
    // Safari concede la condivisione solo subito dopo il tocco: se lo scaricamento è stato lungo
    // il file ora è pronto e basta toccare di nuovo
    if (e.name === 'NotAllowedError') { btn?.classList.add('ready'); fileMsg('File pronto: tocca di nuovo l’icona di condivisione.'); }
    else if (e.name !== 'AbortError') fileMsg('Condivisione non riuscita.');
  } finally { btn?.classList.remove('busy'); }
}
function shareButton(name) {
  const b = el('button', { className: 'share', title: 'Condividi', ariaLabel: 'Condividi ' + name });
  b.innerHTML = $('pvShare').innerHTML; // stessa icona del pannello di anteprima
  b.addEventListener('click', () => shareFile(name, b));
  return b;
}

const KINDS = {
  img: /\.(png|jpe?g|gif|webp|bmp|heic|heif|svg)$/i,
  video: /\.(mp4|mov|m4v|webm)$/i,
  audio: /\.(mp3|m4a|wav|aac|ogg|flac)$/i,
  text: /\.(txt|md|csv|log|json|xml)$/i,
};
let pvUrl = null, pvFile = null;
async function preview(name) {
  pvFile = name;
  $('pvName').textContent = name;
  $('pvBody').replaceChildren(el('div', { className: 'none', textContent: 'Caricamento…' }));
  $('preview').classList.add('show');
  const kind = Object.keys(KINDS).find((k) => KINDS[k].test(name));
  if (!kind) {
    $('pvBody').replaceChildren(el('div', { className: 'none',
      textContent: 'Anteprima non disponibile per questo tipo di file. Usa l’icona di condivisione per aprirlo in un’app dell’iPad, oppure «Apri qui» per aprirlo sul PC su questo schermo.' }));
    return;
  }
  try {
    const f = await getFile(name);
    if (pvFile !== name) return;
    if (pvUrl) URL.revokeObjectURL(pvUrl);
    pvUrl = URL.createObjectURL(f);
    let node;
    if (kind === 'img') node = el('img', { src: pvUrl, alt: name });
    else if (kind === 'video') node = el('video', { src: pvUrl, controls: true, playsInline: true });
    else if (kind === 'audio') node = el('audio', { src: pvUrl, controls: true });
    else node = el('pre', { textContent: f.size > 2e6 ? 'File di testo troppo grande per l’anteprima.' : await f.text() });
    $('pvBody').replaceChildren(node);
  } catch {
    $('pvBody').replaceChildren(el('div', { className: 'none', textContent: 'Impossibile caricare il file dal PC.' }));
  }
}
function closePreview() {
  $('preview').classList.remove('show');
  $('pvBody').replaceChildren();
  if (pvUrl) { URL.revokeObjectURL(pvUrl); pvUrl = null; }
  pvFile = null;
}
$('pvClose').addEventListener('click', closePreview);
$('pvShare').addEventListener('click', () => pvFile && shareFile(pvFile, null));

$('copyShortcut').addEventListener('click', async () => {
  const url = `${location.origin}/api/invia?dev=${encodeURIComponent(devId)}&token=${encodeURIComponent(store.get('token', ''))}`;
  try { await navigator.clipboard.writeText(url); fileMsg('Indirizzo copiato: incollalo nel Comando rapido.'); }
  catch { fileMsg('Copia non riuscita: serve la connessione sicura (vedi sotto).'); }
});

// "Apri qui": il file si apre sul PC con il suo programma, sullo schermo di questo dispositivo
// (es. un PDF in Edge, da firmare con la Pencil)
function openHere(name) {
  const b = el('button', { textContent: 'Apri qui', title: 'Apri sul PC, su questo schermo' });
  b.addEventListener('click', () => { send({ t: 'apri', nome: name }); closeFiles(); });
  return b;
}

function openFiles() { closeMenu(); $('files').classList.add('show'); refreshFiles(); }
function closeFiles() { $('files').classList.remove('show'); }
$('filesBtn').addEventListener('click', openFiles);
$('filesClose').addEventListener('click', closeFiles);
$('files').addEventListener('click', (e) => { if (e.target === $('files')) closeFiles(); });
$('pick').addEventListener('click', () => $('picker').click());

$('picker').addEventListener('change', () => {
  const list = [...$('picker').files];
  $('picker').value = '';
  upload(list);
});

// trascinare file nel pannello (PC, oppure iPad con l'app File affiancata)
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
$('files').addEventListener('dragenter', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth++;
  $('files').classList.add('dragging'); $('drop').classList.add('over');
});
$('files').addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
$('files').addEventListener('dragleave', () => {
  if (--dragDepth <= 0) { dragDepth = 0; $('files').classList.remove('dragging'); $('drop').classList.remove('over'); }
});
$('files').addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0;
  $('files').classList.remove('dragging'); $('drop').classList.remove('over');
  upload([...e.dataTransfer.files].filter((f) => f.size || f.type)); // le cartelle arrivano vuote: le salto
});
// file trascinati sullo schermo con il pannello chiuso: apro il pannello
window.addEventListener('dragenter', (e) => { if (hasFiles(e) && !$('files').classList.contains('show')) openFiles(); });
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', (e) => e.preventDefault());

function upload(list) {
  if (!list.length) return;
  const form = new FormData();
  for (const f of list) form.append('file', f, f.name);
  const bar = $('upload'), fill = bar.firstElementChild, label = bar.lastElementChild;
  bar.classList.remove('hidden'); fill.style.width = '0'; label.textContent = 'Invio…';
  $('pick').disabled = true;
  // XMLHttpRequest e non fetch: serve l'avanzamento del caricamento
  const x = new XMLHttpRequest();
  x.open('POST', api('/api/invia'));
  x.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const p = Math.round(e.loaded / e.total * 100);
    fill.style.width = p + '%'; label.textContent = `Invio… ${p}%`;
  };
  x.onload = () => {
    $('pick').disabled = false;
    label.textContent = x.status === 200 ? `Aggiunti ${list.length} file` : 'Invio non riuscito';
    fill.style.width = '100%';
    setTimeout(() => bar.classList.add('hidden'), 2500);
    refreshFiles();
  };
  x.onerror = () => { $('pick').disabled = false; label.textContent = 'Invio non riuscito: connessione persa'; };
  x.send(form);
}

let toastTimer = null, toastAction = null;
// avviso in basso: testo, pulsante facoltativo (label + azione), durata
function notice(text, label, action, ms = 20000) {
  $('toastText').textContent = text;
  $('toastOpen').textContent = label || '';
  $('toastOpen').hidden = !action;
  toastAction = action;
  $('toast').classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), ms);
}
function showToast(m) {
  if ($('files').classList.contains('show')) refreshFiles();
  if (!m.nomi.length) return; // solo file tolti: aggiorno l'elenco e basta
  const one = m.nomi.length === 1 ? m.nomi[0] : null;
  notice(one ? `Nuovo file da ${m.da}: ${one}` : `${m.nomi.length} nuovi file da ${m.da}`, 'Vedi', () => {
    openFiles();
    if (one) preview(one);
  });
}
$('toastOpen').addEventListener('click', () => {
  $('toast').classList.remove('show');
  if (toastAction) toastAction();
});

// Appunti condivisi. Safari scrive e legge gli appunti solo dopo un tocco: per questo "Copia" e "Invia".
function clipFromPC(m) {
  if (m.tipo === 'testo') {
    const short = m.testo.length > 40 ? m.testo.slice(0, 40).trim() + '…' : m.testo;
    notice(`Appunti dal PC: “${short.replace(/\s+/g, ' ')}”`, 'Copia', async () => {
      try { await navigator.clipboard.writeText(m.testo); notice('Copiato negli appunti', null, null, 3000); }
      catch { notice('Copia non riuscita', null, null, 4000); }
    });
  } else {
    notice('Immagine negli appunti del PC', 'Copia', async () => {
      try {
        // la promessa va passata subito, dentro il tocco: Safari aspetta il download
        const png = fetch(api('/api/appunti/immagine')).then((r) => { if (!r.ok) throw new Error(); return r.blob(); });
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
        notice('Immagine copiata negli appunti', null, null, 3000);
      } catch { notice("Copia dell'immagine non riuscita", null, null, 4000); }
    });
  }
}
// Scrivere sul PC: testo o credenziali (anche scelte dalle Password dell'iPad) nel campo selezionato sul PC
function typeOnPC(enter) {
  const user = $('typeUser').value, pass = $('typePass').value, text = $('typeText').value;
  if (text) send({ t: 'scrivi', testo: text });
  if (user) { send({ t: 'scrivi', testo: user }); if (pass) send({ t: 'tasto', k: 'tab' }); }
  if (pass) send({ t: 'scrivi', testo: pass, segreto: true });
  if (enter) send({ t: 'tasto', k: 'invio' });
  for (const id of ['typeUser', 'typePass', 'typeText']) $(id).value = '';
  document.activeElement.blur();
  if (user || pass || text) notice('Scritto sul PC', null, null, 3000);
}
$('typeGo').addEventListener('click', () => typeOnPC(false));
$('typeEnter').addEventListener('click', () => typeOnPC(true));

$('clipSend').addEventListener('click', async () => {
  try {
    for (const item of await navigator.clipboard.read()) {
      const img = item.types.find((t) => t.startsWith('image/'));
      if (img) {
        const r = await fetch(api('/api/appunti/immagine'), { method: 'POST', body: await item.getType(img) });
        notice(r.ok ? 'Immagine copiata negli appunti del PC' : 'Invio non riuscito', null, null, 4000);
        return;
      }
      if (item.types.includes('text/plain')) {
        send({ t: 'appunti', testo: await (await item.getType('text/plain')).text() });
        notice('Testo copiato negli appunti del PC', null, null, 4000);
        return;
      }
    }
    notice("Gli appunti dell'iPad sono vuoti", null, null, 3000);
  } catch {
    notice('Safari non ha dato gli appunti: tocca "Incolla" quando compare', null, null, 5000);
  }
});
$('toastClose').addEventListener('click', () => $('toast').classList.remove('show'));

setMode(mode);
setPenMode(penMode);
document.querySelectorAll('#quality button').forEach((b) => b.setAttribute('aria-pressed', +b.dataset.q === quality));
connect();
