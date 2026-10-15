// Minimal WebGL viewer for binary STL parts: orbit (drag), zoom (wheel), smooth shading
// with crisp edges, optional print bed. Usage:
//   const v = createViewer(canvas);
//   v.show([{ buf, color: "#c4bfa6", offset: [x, y, z] }, ...], { bed: [180, 180] });
"use strict";

function createViewer(canvas) {
  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) return { show() {}, setColor() {}, reset() {}, top() {} };
  const vs = `attribute vec3 p; attribute vec3 n; uniform mat4 mvp; uniform vec3 off; varying vec3 vn;
    void main() { gl_Position = mvp * vec4(p + off, 1.0); vn = n; }`;
  const fs = `precision mediump float; varying vec3 vn; uniform vec3 col;
    void main() {
      vec3 n = normalize(vn);
      float d = max(dot(n, normalize(vec3(-0.5, 0.6, 0.9))), 0.0);
      float f = max(dot(n, normalize(vec3(0.6, -0.4, 0.3))), 0.0);
      float sky = 0.5 + 0.5 * n.z;
      gl_FragColor = vec4(min(col * (0.22 + 0.22 * sky + 0.55 * d + 0.12 * f), 1.0), 1.0);
    }`;
  const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(prog); gl.useProgram(prog);
  const L = { p: gl.getAttribLocation(prog, "p"), n: gl.getAttribLocation(prog, "n"),
              mvp: gl.getUniformLocation(prog, "mvp"), off: gl.getUniformLocation(prog, "off"),
              col: gl.getUniformLocation(prog, "col") };
  gl.enable(gl.DEPTH_TEST);
  const hex = (h) => [1, 3, 5].map(k => parseInt(h.slice(k, k + 2), 16) / 255);

  let items = [], center = [0, 0, 0], radius = 100;
  const cam = { theta: -Math.PI / 2, phi: 0.7, dist: 2.3 };

  function parse(buf) {
    const dv = new DataView(buf), n = dv.getUint32(80, true);
    const pos = new Float32Array(n * 9), nrm = new Float32Array(n * 9);
    for (let i = 0; i < n; i++) for (let k = 0; k < 9; k++) pos[i * 9 + k] = dv.getFloat32(84 + i * 50 + 12 + k * 4, true);
    const key = (i) => `${Math.round(pos[i] * 200)},${Math.round(pos[i + 1] * 200)},${Math.round(pos[i + 2] * 200)}`;
    const face = new Float32Array(n * 3), acc = new Map(), keys = new Array(n * 3);
    for (let t = 0; t < n; t++) {
      const b = t * 9;
      const ux = pos[b + 3] - pos[b], uy = pos[b + 4] - pos[b + 1], uz = pos[b + 5] - pos[b + 2];
      const vx = pos[b + 6] - pos[b], vy = pos[b + 7] - pos[b + 1], vz = pos[b + 8] - pos[b + 2];
      const ax = uy * vz - uz * vy, ay = uz * vx - ux * vz, az = ux * vy - uy * vx;
      const l = Math.hypot(ax, ay, az) || 1;
      face[t * 3] = ax / l; face[t * 3 + 1] = ay / l; face[t * 3 + 2] = az / l;
      for (let k = 0; k < 3; k++) {
        const kk = key(b + k * 3); keys[t * 3 + k] = kk;
        if (az / l < 0.2) continue;  // only up-facing (terrain-like) faces are smoothed
        const a = acc.get(kk); if (a) { a[0] += ax; a[1] += ay; a[2] += az; } else acc.set(kk, [ax, ay, az]);
      }
    }
    for (let t = 0; t < n; t++) {
      const fx = face[t * 3], fy = face[t * 3 + 1], fz = face[t * 3 + 2];
      for (let k = 0; k < 3; k++) {
        let nx = fx, ny = fy, nz = fz;
        const a = acc.get(keys[t * 3 + k]);
        if (a) {
          const l = Math.hypot(a[0], a[1], a[2]) || 1;
          if ((a[0] * fx + a[1] * fy + a[2] * fz) / l > 0.5) { nx = a[0] / l; ny = a[1] / l; nz = a[2] / l; }
        }
        nrm.set([nx, ny, nz], t * 9 + k * 3);
      }
    }
    return { pos, nrm, count: n * 3 };
  }
  function buffer(arr) { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, arr, gl.STATIC_DRAW); return b; }
  function slab(w, d, x0, y0) {  // thin build plate under the parts
    const z = -0.6, t = 0.6, P = [], N = [];
    const quads = [[[x0, y0, z + t], [x0 + w, y0, z + t], [x0 + w, y0 + d, z + t], [x0, y0 + d, z + t], [0, 0, 1]],
                   [[x0, y0, z], [x0 + w, y0, z], [x0 + w, y0, z + t], [x0, y0, z + t], [0, -1, 0]]];
    for (const [a, b, c, e, n] of quads) for (const v of [a, b, c, a, c, e]) { P.push(...v); N.push(...n); }
    return { pos: new Float32Array(P), nrm: new Float32Array(N), count: P.length / 3 };
  }

  function show(parts, opts = {}) {
    items.forEach(it => { gl.deleteBuffer(it.p); gl.deleteBuffer(it.n); });
    items = parts.map(pt => {
      const m = parse(pt.buf);
      return { p: buffer(m.pos), n: buffer(m.nrm), count: m.count, off: pt.offset || [0, 0, 0], col: hex(pt.color) };
    });
    // Frame everything by the first part's bounds (the largest one).
    let lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    parts.forEach((pt, i) => {
      const dv = new DataView(pt.buf), n = dv.getUint32(80, true), o = pt.offset || [0, 0, 0];
      for (let t = 0; t < n; t += Math.max(1, Math.floor(n / 4000))) for (let k = 0; k < 3; k++) for (let c = 0; c < 3; c++) {
        const v = dv.getFloat32(84 + t * 50 + 12 + k * 12 + c * 4, true) + o[c];
        lo[c] = Math.min(lo[c], v); hi[c] = Math.max(hi[c], v);
      }
    });
    center = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (hi[2] - lo[2]) / 3];
    radius = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2;
    if (opts.bed) {
      const [bx, by] = opts.bed, s = slab(bx, by, center[0] - bx / 2, center[1] - by / 2);
      items.push({ p: buffer(s.pos), n: buffer(s.nrm), count: s.count, off: [0, 0, 0], col: hex(opts.bedColor || "#c9c7c2") });
    }
    redraw();
  }
  function setColor(i, color) { if (items[i]) { items[i].col = hex(color); redraw(); } }

  const persp = (f, a, n, fa) => { const t = 1 / Math.tan(f / 2), nf = 1 / (n - fa); return [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (fa + n) * nf, -1, 0, 0, 2 * fa * n * nf, 0]; };
  function look(e, c, u) {
    const sub = (a, b) => a.map((v, i) => v - b[i]), nrm = (a) => { const l = Math.hypot(...a) || 1; return a.map(v => v / l); };
    const cr = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const z = nrm(sub(e, c)), x = nrm(cr(u, z)), y = cr(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1];
  }
  const mul = (a, b) => { const o = new Array(16).fill(0); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) o[i * 4 + j] += a[k * 4 + j] * b[i * 4 + k]; return o; };

  function draw() {
    const dpr = devicePixelRatio || 1, W = Math.round(canvas.clientWidth * dpr), H = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
    gl.viewport(0, 0, W, H);
    const bg = getComputedStyle(canvas.parentElement).backgroundColor.match(/\d+/g).map(Number);
    gl.clearColor(bg[0] / 255, bg[1] / 255, bg[2] / 255, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!items.length) return;
    const d = cam.dist * radius;
    const eye = [center[0] + d * Math.cos(cam.phi) * Math.cos(cam.theta), center[1] + d * Math.cos(cam.phi) * Math.sin(cam.theta), center[2] + d * Math.sin(cam.phi)];
    const mvp = mul(persp(0.6, W / H, radius * 0.05, radius * 20), look(eye, center, cam.phi > 1.55 ? [0, 1, 0] : [0, 0, 1]));
    gl.uniformMatrix4fv(L.mvp, false, new Float32Array(mvp));
    for (const it of items) {
      gl.uniform3fv(L.off, it.off); gl.uniform3fv(L.col, it.col);
      gl.bindBuffer(gl.ARRAY_BUFFER, it.p); gl.enableVertexAttribArray(L.p); gl.vertexAttribPointer(L.p, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, it.n); gl.enableVertexAttribArray(L.n); gl.vertexAttribPointer(L.n, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.TRIANGLES, 0, it.count);
    }
  }
  let frame = 0;
  const redraw = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; draw(); }); };
  let drag = null;
  canvas.addEventListener("pointerdown", (e) => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cam.theta -= (e.clientX - drag[0]) * 0.008;
    cam.phi = Math.min(1.5707, Math.max(0.05, cam.phi + (e.clientY - drag[1]) * 0.008));
    drag = [e.clientX, e.clientY]; redraw();
  });
  canvas.addEventListener("pointerup", () => (drag = null));
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); cam.dist = Math.min(8, Math.max(0.6, cam.dist * Math.exp(e.deltaY * 0.001))); redraw(); }, { passive: false });
  new ResizeObserver(redraw).observe(canvas);
  return {
    show, setColor,
    reset() { cam.theta = -Math.PI / 2; cam.phi = 0.7; cam.dist = 2.3; redraw(); },
    top() { cam.theta = -Math.PI / 2; cam.phi = 1.5707; cam.dist = 2.4; redraw(); },
  };
}
