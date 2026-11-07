'use strict';
/* Interface language (PC window and device page): Italian or English.

   Texts stay written in Italian; the translation is in /lingue/en.json (the same file as the server):
   - "testi": Italian sentence -> English, with {} in place of the changing parts ({1}, {2}... to change their order);
   - "valori": words that end up inside sentences ("a destra", "Massima"...).
   The page translates itself: every text that appears (including those written later by the program) is translated,
   and the Italian original is kept aside to switch back. An element with data-i18n-html is translated as a whole
   (sentences with bold words or links inside); translate="no" excludes file names, devices, addresses. */
const Lingua = (() => {
  let lang = 'it';
  const exact = new Map(), values = new Map(), pats = [];
  const listeners = [];
  const ready = fetch('/lingue/en.json', { cache: 'no-cache' }).then((r) => r.json()).then((d) => {
    // uniform spaces: in the page texts arrive with double spaces reduced to one
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
  /** The text in the chosen language (in Italian, or without a translation, it stays as it is). */
  function t(s) {
    if (lang === 'it' || !s || !LETTERS.test(s)) return s;
    const core = s.trim();
    const r = translate(core.replace(/\s+/g, ' '));  // HTML texts written over several lines
    if (r == null) return s;
    const i = s.indexOf(core);
    return s.slice(0, i) + r + s.slice(i + core.length);
  }

  /* ---------- automatic page translation ---------- */
  const ATTRS = ['title', 'aria-label', 'placeholder', 'alt', 'data-tip-text'];
  const texts = new WeakMap();   // text node -> [original, shown]
  const attrs = new WeakMap();   // element -> { attribute: [original, shown] }
  const htmls = new WeakMap();   // data-i18n-html element -> [original, shown]
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
    try { localStorage.setItem('lingua', l); } catch { /* no storage: fine anyway */ }
    ready.then(() => {
      walk(document.documentElement);
      document.querySelectorAll('.lingue').forEach(mark);
      if (changed) listeners.forEach((fn) => fn(l));
    });
  }

  /* ---------- picker: flag and country code ---------- */
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
  const span = (cls, text) => Object.assign(document.createElement('span'), { className: cls, textContent: text });
  /** The button shows the current language; in the list the chosen one is marked. */
  function mark(box) {
    const [code, short] = LANGS.find((l) => l[0] === lang) || LANGS[0];
    box.querySelector('.lingua-scelta').replaceChildren(flag(code), span('sigla', short), span('freccia', '▾'));
    box.querySelectorAll('[role="option"]').forEach((o) => o.setAttribute('aria-selected', o.dataset.lingua === lang));
  }
  /** Dropdown: a button with flag and code (🇮🇹 IT, 🇬🇧 GB) that opens the list of languages.
      The flags are drawn: Windows doesn't show flag emoji (only the letters). onPick(language) on choice. */
  function picker(onPick) {
    const box = document.createElement('div');
    box.className = 'lingue';
    box.setAttribute('translate', 'no');
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'lingua-scelta';
    btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', 'Lingua / Language');
    const list = document.createElement('div');
    list.className = 'lingua-lista'; list.setAttribute('role', 'listbox'); list.hidden = true;
    const close = () => { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    for (const [code, short, name] of LANGS) {
      const o = document.createElement('button');
      o.type = 'button'; o.setAttribute('role', 'option'); o.dataset.lingua = code;
      o.append(flag(code), span('sigla', short), span('nome', name));
      o.addEventListener('click', () => { close(); btn.focus(); use(code); onPick(code); });
      list.append(o);
    }
    btn.addEventListener('click', () => {
      const open = list.hidden;
      list.hidden = !open; btn.setAttribute('aria-expanded', open);
      if (open) (list.querySelector('[aria-selected="true"]') || list.firstElementChild).focus();
    });
    document.addEventListener('click', (e) => { if (!box.contains(e.target)) close(); });
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); close(); btn.focus(); return; }
      if (list.hidden || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
      e.preventDefault();
      const opts = [...list.children], i = opts.indexOf(document.activeElement);
      opts[(i + (e.key === 'ArrowDown' ? 1 : -1) + opts.length) % opts.length].focus();
    });
    box.append(btn, list);
    mark(box);
    return box;
  }

  return { t, use, picker, ready, get lang() { return lang; },
    locale: () => (lang === 'en' ? 'en-GB' : 'it-IT'),
    onChange: (fn) => listeners.push(fn) };
})();
