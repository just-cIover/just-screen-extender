'use strict';
/* Lingua dell'interfaccia (finestra del PC e pagina dei dispositivi): italiano o inglese.

   I testi restano scritti in italiano; la traduzione sta in /lingue/en.json (lo stesso file del server):
   - "testi": frase italiana -> inglese, con {} al posto delle parti che cambiano ({1}, {2}... per cambiarne l'ordine);
   - "valori": parole che finiscono dentro le frasi ("a destra", "Massima"...).
   La pagina si traduce da sola: ogni testo che compare (anche quelli scritti dopo dal programma) viene tradotto,
   e l'originale italiano resta da parte per tornare indietro. Un elemento con data-i18n-html si traduce intero
   (frasi con parole in grassetto o link in mezzo); translate="no" esclude nomi di file, dispositivi, indirizzi. */
const Lingua = (() => {
  let lang = 'it';
  const exact = new Map(), values = new Map(), pats = [];
  const listeners = [];
  const ready = fetch('/lingue/en.json', { cache: 'no-cache' }).then((r) => r.json()).then((d) => {
    // spazi uniformi: nella pagina i testi arrivano con gli spazi doppi ridotti a uno
    const norm = (s) => s.replace(/\s+/g, ' ');
    for (const [k, v] of Object.entries(d.valori || {})) values.set(norm(k), v);
    for (const [raw, v] of Object.entries(d.testi || {})) {
      const k = norm(raw);
      if (!k.includes('{}')) { exact.set(k, v); continue; }
      const rx = new RegExp('^' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{\\\}/g, '(.+?)') + '$', 's');
      pats.push([k.length, rx, v]);
    }
    pats.sort((a, b) => b[0] - a[0]);
  }).catch(() => {});

  function translate(s) {
    if (exact.has(s)) return exact.get(s);
    if (values.has(s)) return values.get(s);
    for (const [, rx, v] of pats) {
      const m = s.match(rx);
      if (m) return fill(v, m.slice(1));
    }
    return null;
  }
  function part(s) {
    if (values.has(s)) return values.get(s);
    const r = translate(s);
    if (r != null) return r;
    for (const [sep, out] of [['  oppure  ', '  or  '], [' oppure ', ' or '], [', ', ', ']]) {
      if (s.includes(sep)) return s.split(sep).map((x) => values.get(x) ?? translate(x) ?? x).join(out);
    }
    return s;
  }
  function fill(tpl, groups) {
    const parts = groups.map(part);
    let i = 0;
    return tpl.replace(/\{(\d*)\}/g, (_, n) => (n ? parts[+n - 1] ?? '' : parts[i++] ?? ''));
  }
  const LETTERS = /[a-zà-ú]/i;
  /** Il testo nella lingua scelta (in italiano, o senza traduzione, resta com'è). */
  function t(s) {
    if (lang === 'it' || !s || !LETTERS.test(s)) return s;
    const core = s.trim();
    const r = translate(core.replace(/\s+/g, ' '));  // testi dell'HTML scritti su più righe
    if (r == null) return s;
    const i = s.indexOf(core);
    return s.slice(0, i) + r + s.slice(i + core.length);
  }

  /* ---------- traduzione automatica della pagina ---------- */
  const ATTRS = ['title', 'aria-label', 'placeholder', 'alt', 'data-tip-text'];
  const texts = new WeakMap();   // nodo di testo -> [originale, mostrato]
  const attrs = new WeakMap();   // elemento -> { attributo: [originale, mostrato] }
  const htmls = new WeakMap();   // elemento data-i18n-html -> [originale, mostrato]
  const excluded = (e) => !e || !!e.closest('script, style, [translate="no"]');
  function doText(n) {
    const e = n.parentElement;
    if (excluded(e) || e.closest('[data-i18n-html]')) return;
    let r = texts.get(n);
    if (!r || n.data !== r[1]) { r = [n.data, null]; texts.set(n, r); }
    r[1] = t(r[0]);
    if (n.data !== r[1]) n.data = r[1];
  }
  function doAttr(e, a) {
    if (excluded(e) || !e.hasAttribute(a)) return;
    const all = attrs.get(e) || {};
    attrs.set(e, all);
    const v = e.getAttribute(a);
    let r = all[a];
    if (!r || v !== r[1]) { r = all[a] = [v, null]; }
    r[1] = t(r[0]);
    if (v !== r[1]) e.setAttribute(a, r[1]);
  }
  function doHtml(e) {
    let r = htmls.get(e);
    const now = e.innerHTML;
    if (!r || now !== r[1]) { r = [now, null]; htmls.set(e, r); }
    const key = r[0].replace(/\s+/g, ' ').trim();
    const tr = lang === 'it' ? null : translate(key);
    r[1] = tr == null ? r[0] : tr;
    if (now !== r[1]) e.innerHTML = r[1];
  }
  function walk(root) {
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 || excluded(root)) return;
    const all = [root, ...root.querySelectorAll('*')];
    for (const e of all) {
      if (e.matches('[data-i18n-html]')) { if (!excluded(e)) doHtml(e); continue; }
      for (const a of ATTRS) if (e.hasAttribute(a)) doAttr(e, a);
    }
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) doText(n);
  }
  const observer = new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === 'characterData') doText(m.target);
      else if (m.type === 'attributes') doAttr(m.target, m.attributeName);
      else m.addedNodes.forEach(walk);
    }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ATTRS });

  function use(l) {
    if (l !== 'it' && l !== 'en') return;
    const changed = l !== lang;
    lang = l;
    document.documentElement.lang = l;
    try { localStorage.setItem('lingua', l); } catch { /* niente memoria: va bene lo stesso */ }
    ready.then(() => {
      walk(document.documentElement);
      document.querySelectorAll('.lingue').forEach(mark);
      if (changed) listeners.forEach((fn) => fn(l));
    });
  }

  /* ---------- selettore: bandiera e sigla del paese ---------- */
  let uid = 0;
  function flag(code) {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 60 40'); s.setAttribute('aria-hidden', 'true'); s.classList.add('bandiera');
    if (code === 'it') {
      s.innerHTML = '<rect width="20" height="40" fill="#009246"/><rect x="20" width="20" height="40" fill="#fff"/>' +
        '<rect x="40" width="20" height="40" fill="#ce2b37"/>';
    } else {
      const id = 'gb' + (++uid);
      s.setAttribute('preserveAspectRatio', 'xMidYMid slice');
      s.setAttribute('viewBox', '5 0 50 30');
      s.innerHTML = `<clipPath id="${id}"><path d="M30,15 h30 v15 z v15 h-30 z h-30 v-15 z v-15 h30 z"/></clipPath>` +
        '<rect width="60" height="30" fill="#012169"/>' +
        '<path d="M0,0 L60,30 M60,0 L0,30" stroke="#fff" stroke-width="6"/>' +
        `<path d="M0,0 L60,30 M60,0 L0,30" clip-path="url(#${id})" stroke="#c8102e" stroke-width="4"/>` +
        '<path d="M30,0 v30 M0,15 h60" stroke="#fff" stroke-width="10"/>' +
        '<path d="M30,0 v30 M0,15 h60" stroke="#c8102e" stroke-width="6"/>';
    }
    return s;
  }
  const LANGS = [['it', 'IT', 'Italiano'], ['en', 'GB', 'English']];
  function mark(box) {
    box.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.lingua === lang));
  }
  /** Due pulsanti 🇮🇹 IT / 🇬🇧 GB; onPick(lingua) quando ne tocchi uno. */
  function picker(onPick) {
    const box = document.createElement('div');
    box.className = 'lingue'; box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'Lingua');
    box.setAttribute('translate', 'no');
    for (const [code, short, name] of LANGS) {
      const b = document.createElement('button');
      b.type = 'button'; b.dataset.lingua = code; b.title = name;
      b.append(flag(code), short);
      b.addEventListener('click', () => { use(code); onPick(code); });
      box.append(b);
    }
    mark(box);
    return box;
  }

  return { t, use, picker, ready, get lang() { return lang; },
    locale: () => (lang === 'en' ? 'en-GB' : 'it-IT'),
    onChange: (fn) => listeners.push(fn) };
})();
