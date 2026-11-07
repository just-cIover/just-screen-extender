'use strict';
// Disegno dei fotogrammi decodificati. Safari non disegna i VideoFrame allo stesso modo
// su tutte le versioni: al primo fotogramma provo più metodi su una tela piccola e
// tengo il primo che produce un'immagine vera (non nera).
//   2d     -> drawImage(VideoFrame)                  (il più veloce, dove funziona)
//   bitmap -> createImageBitmap + drawImage
//   webgl  -> texImage2D(VideoFrame)
//   yuv    -> copyTo dei piani Y/UV + conversione in shader (funziona sempre, più pesante)

const METHODS = ['2d', 'bitmap', 'webgl', 'yuv'];

const VS = `attribute vec2 p; varying vec2 uv;
void main(){ uv = vec2((p.x+1.0)*0.5, (1.0-p.y)*0.5); gl_Position = vec4(p,0.0,1.0); }`;
const FS_RGB = `precision mediump float; varying vec2 uv; uniform sampler2D t; uniform bool bgr;
void main(){ vec4 c = texture2D(t, uv); gl_FragColor = vec4(bgr ? c.bgr : c.rgb, 1.0); }`;
const FS_YUV = `precision mediump float; varying vec2 uv; uniform sampler2D ty; uniform sampler2D tu; uniform sampler2D tv;
uniform bool nv12;
void main(){
  float y = texture2D(ty, uv).r;
  vec2 c = nv12 ? texture2D(tu, uv).ra : vec2(texture2D(tu, uv).r, texture2D(tv, uv).r);
  y = (y - 0.0625) * 1.164; float u = c.x - 0.5; float v = c.y - 0.5;
  gl_FragColor = vec4(y + 1.793*v, y - 0.213*u - 0.533*v, y + 2.112*u, 1.0);
}`;

class GL {
  constructor(canvas, yuv) {
    const gl = canvas.getContext('webgl', { alpha: false, antialias: false, depth: false, preserveDrawingBuffer: true });
    if (!gl) throw new Error('webgl non disponibile');
    this.gl = gl; this.yuv = yuv;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, yuv ? FS_YUV : FS_RGB));
    gl.linkProgram(pr); gl.useProgram(pr); this.pr = pr;
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    this.tex = (yuv ? ['ty', 'tu', 'tv'] : ['t']).map((name, i) => {
      const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, t);
      for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
        [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
      gl.uniform1i(gl.getUniformLocation(pr, name), i);
      return t;
    });
    this.uBgr = gl.getUniformLocation(pr, 'bgr');
    this.uNv12 = gl.getUniformLocation(pr, 'nv12');
    this.buf = null;
  }

  drawFrame(frame) {
    const gl = this.gl;
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.tex[0]);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, frame);
    gl.uniform1i(this.uBgr, false);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  async drawPlanes(frame) {
    const gl = this.gl;
    const size = frame.allocationSize();
    if (!this.buf || this.buf.byteLength !== size) this.buf = new Uint8Array(size);
    const layout = await frame.copyTo(this.buf);
    const w = frame.codedWidth, h = frame.codedHeight, f = frame.format;
    const plane = (i, pw, ph, fmt, bpp) => {
      gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, this.tex[i]);
      const { offset, stride } = layout[i];
      // le righe possono avere padding: carico la larghezza intera della riga e ritaglio in shader non serve,
      // perché lo stride in pixel coincide quasi sempre con la larghezza; altrimenti copio riga per riga
      let data;
      if (stride === pw * bpp) data = this.buf.subarray(offset, offset + stride * ph);
      else {
        data = new Uint8Array(pw * bpp * ph);
        for (let r = 0; r < ph; r++) data.set(this.buf.subarray(offset + r * stride, offset + r * stride + pw * bpp), r * pw * bpp);
      }
      gl.texImage2D(gl.TEXTURE_2D, 0, fmt, pw, ph, 0, fmt, gl.UNSIGNED_BYTE, data);
    };
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    if (f === 'NV12') {
      plane(0, w, h, gl.LUMINANCE, 1);
      plane(1, w / 2, h / 2, gl.LUMINANCE_ALPHA, 2);
      gl.uniform1i(this.uNv12, true);
    } else if (f === 'I420') {
      plane(0, w, h, gl.LUMINANCE, 1);
      plane(1, w / 2, h / 2, gl.LUMINANCE, 1);
      plane(2, w / 2, h / 2, gl.LUMINANCE, 1);
      gl.uniform1i(this.uNv12, false);
    } else {
      throw new Error('formato ' + f + ' non gestito');
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}

// luminosità media (0-255) dei pixel letti da una tela 2d o webgl
function brightness(canvas, kind) {
  let px;
  if (kind === '2d') px = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  else {
    const gl = canvas.getContext('webgl');
    px = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
  }
  let s = 0;
  for (let i = 0; i < px.length; i += 4) s += px[i] + px[i + 1] + px[i + 2];
  return s / (px.length / 4) / 3;
}

// luminosità "vera" del fotogramma, letta dai dati grezzi (piano Y o RGB)
async function frameBrightness(frame) {
  if (!frame.format) return null;
  const buf = new Uint8Array(frame.allocationSize());
  const layout = await frame.copyTo(buf);
  const { offset, stride } = layout[0];
  const rgb = !/^(I4|NV)/.test(frame.format);
  let s = 0, n = 0;
  for (let r = 0; r < frame.codedHeight; r += 16) {
    for (let c = 0; c < frame.codedWidth; c += 16) {
      if (rgb) { const i = offset + r * stride + c * 4; s += (buf[i] + buf[i + 1] + buf[i + 2]) / 3; }
      else s += buf[offset + r * stride + c];
      n++;
    }
  }
  return s / n;
}

function makeCanvas(method, w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  let draw;
  if (method === '2d' || method === 'bitmap') {
    const ctx = c.getContext('2d', { alpha: false });
    draw = method === '2d'
      ? (f) => ctx.drawImage(f, 0, 0, c.width, c.height)
      : async (f) => { const b = await createImageBitmap(f); ctx.drawImage(b, 0, 0, c.width, c.height); b.close(); };
  } else {
    const g = new GL(c, method === 'yuv');
    draw = method === 'webgl' ? (f) => g.drawFrame(f) : (f) => g.drawPlanes(f);
  }
  return { canvas: c, draw, kind: method === '2d' || method === 'bitmap' ? '2d' : 'webgl' };
}

class Renderer {
  constructor(stage, onReport, forced = null) {
    this.stage = stage;
    this.onReport = onReport;
    this.forced = forced;  // metodo imposto (senza prova)
    this.method = null;
    this.out = null;
    this.busy = false;
    this.testing = false;
    this.canvas = document.createElement('canvas');
    this.canvas.width = 4; this.canvas.height = 3;
    stage.append(this.canvas);
  }

  get width() { return this.canvas.width; }
  get height() { return this.canvas.height; }

  async pick(frame) {
    const report = { format: frame.format, w: frame.displayWidth, h: frame.displayHeight, tries: {} };
    let ref = null;
    try { ref = await frameBrightness(frame); } catch (e) { report.refErr = String(e); }
    report.ref = ref == null ? null : Math.round(ref);
    // con un fotogramma davvero scuro il test non direbbe nulla: riprovo al prossimo
    if (ref != null && ref < 12) return null;
    const saved = (() => { try { return localStorage.getItem('renderer'); } catch { return null; } })();
    const order = saved ? [saved, ...METHODS.filter((m) => m !== saved)] : METHODS;
    for (const m of order) {
      try {
        const t = makeCanvas(m, 64, 48);
        await t.draw(frame);
        const b = brightness(t.canvas, t.kind);
        report.tries[m] = Math.round(b);
        if (b > 6) { report.chosen = m; break; }
      } catch (e) {
        report.tries[m] = 'errore: ' + (e && e.message ? e.message : e);
      }
    }
    if (!report.chosen) report.chosen = 'yuv';
    this.onReport(report);
    try { localStorage.setItem('renderer', report.chosen); } catch {}
    return report.chosen;
  }

  use(method, w, h) {
    const o = makeCanvas(method, w, h);
    this.canvas.replaceWith(o.canvas);
    this.canvas = o.canvas;
    this.out = o;
    this.method = method;
  }

  // ritorna true se il fotogramma è stato disegnato
  async draw(frame) {
    if (this.busy || this.testing) { frame.close(); return false; }
    try {
      if (!this.method) {
        this.testing = true;
        const m = this.forced || await this.pick(frame);
        if (this.forced) this.onReport({ format: frame.format, w: frame.displayWidth, h: frame.displayHeight, chosen: m, imposto: true });
        this.testing = false;
        if (!m) return false;
        this.use(m, frame.displayWidth, frame.displayHeight);
      }
      if (this.canvas.width !== frame.displayWidth || this.canvas.height !== frame.displayHeight) {
        this.use(this.method, frame.displayWidth, frame.displayHeight);
      }
      this.busy = true;
      await this.out.draw(frame);
      return true;
    } catch (e) {
      // il metodo scelto ha smesso di funzionare: al prossimo fotogramma rifaccio la scelta
      this.onReport({ error: String(e && e.message ? e.message : e), method: this.method });
      try { localStorage.removeItem('renderer'); } catch {}
      this.method = null;
      return false;
    } finally {
      this.busy = false;
      this.testing = false;
      frame.close();
    }
  }
}
