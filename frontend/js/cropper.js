// Picture cropper: one crop per platform, each in that platform's best shape and size.
// Shapes and size limits come from the registry (/api/platforms: image_formats,
// image_max_bytes), never from here. Hand-built on <canvas>: no library, no build step.
//
//   const crops = await openCropper(src, platforms)   // null if cancelled
//   crops = { linkedin: { blob, label, width, height }, ... }
//
// Output is JPEG at exactly the platform's size, compressed (and if needed shrunk)
// until it fits the platform's limit (Bluesky: 1 MB). Animated GIFs become still.

import { fmt, h } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('crop-dialog');
const canvas = $('crop-canvas');
const ctx = canvas.getContext('2d');
const MAX_STAGE = 640;

let img = null;          // the loaded picture
let platforms = [];      // [{id, name, color, image_formats, image_max_bytes}]
let crops = {};          // id -> { format: index, zoom, cx, cy }  (cx, cy: crop centre, 0..1 of the picture)
let current = null;      // platform id being edited
let finish = null;       // resolves the promise from openCropper

const fmtOf = (id) => { const p = platforms.find((x) => x.id === id); return p.image_formats[crops[id].format]; };

/** The crop rectangle in picture pixels for a platform's current settings. */
function region(id) {
  const [, w, hgt] = fmtOf(id);
  const aspect = w / hgt;
  const c = crops[id];
  const full = Math.min(img.width, img.height * aspect);
  const cw = full / c.zoom;
  const ch = cw / aspect;
  const x = Math.min(Math.max(c.cx * img.width - cw / 2, 0), img.width - cw);
  const y = Math.min(Math.max(c.cy * img.height - ch / 2, 0), img.height - ch);
  c.cx = (x + cw / 2) / img.width;     // keep the centre inside the picture
  c.cy = (y + ch / 2) / img.height;
  return { x, y, cw, ch };
}

function draw() {
  const [label, w, hgt] = fmtOf(current);
  const stageW = Math.min(MAX_STAGE, $('crop-stage').clientWidth || MAX_STAGE);
  canvas.width = stageW;
  canvas.height = Math.round(stageW * hgt / w);
  const { x, y, cw, ch } = region(current);
  ctx.fillStyle = '#ffffff'; // transparent pictures get a white background, as JPEG will
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, x, y, cw, ch, 0, 0, canvas.width, canvas.height);
  $('crop-zoom').value = String(crops[current].zoom);
  const small = cw < w ? ' The picture is smaller than this, so it will be enlarged and may look soft.' : '';
  $('crop-out').textContent = `${label}: ${w}×${hgt} pixels.${small}`;
}

function renderTabs() {
  $('crop-platforms').replaceChildren(...platforms.map((p) => h('label', {},
    h('input', { type: 'radio', name: 'crop-platform', value: p.id, checked: p.id === current,
      onchange: () => { current = p.id; renderTabs(); draw(); } }),
    h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' }), ` ${p.name}`)));
  const p = platforms.find((x) => x.id === current);
  $('crop-formats').replaceChildren(...p.image_formats.map(([label, w, hgt], i) => h('button', {
    type: 'button', class: `btn btn-sm ${crops[current].format === i ? 'btn-primary' : 'btn-ghost'}`,
    'aria-pressed': String(crops[current].format === i),
    onclick: () => { crops[current].format = i; renderTabs(); draw(); },
  }, `${label} ${w}×${hgt}${i === 0 ? ' (best)' : ''}`)));
}

// ---- moving and zooming ----

let drag = null;
canvas.addEventListener('pointerdown', (e) => {
  drag = { x: e.clientX, y: e.clientY };
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', (e) => {
  if (!drag) return;
  const { cw, ch } = region(current);
  const c = crops[current];
  c.cx -= ((e.clientX - drag.x) * cw / canvas.width) / img.width;
  c.cy -= ((e.clientY - drag.y) * ch / canvas.height) / img.height;
  drag = { x: e.clientX, y: e.clientY };
  draw();
});
canvas.addEventListener('pointerup', () => { drag = null; });
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  zoomBy(e.deltaY < 0 ? 0.1 : -0.1);
}, { passive: false });
canvas.addEventListener('keydown', (e) => {
  const step = 0.02;
  const c = crops[current];
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[e.key]) {
    c.cx += moves[e.key][0];
    c.cy += moves[e.key][1];
  } else if (e.key === '+' || e.key === '=') zoomBy(0.1);
  else if (e.key === '-') zoomBy(-0.1);
  else return;
  e.preventDefault();
  draw();
});
$('crop-zoom').addEventListener('input', (e) => { crops[current].zoom = Number(e.target.value); draw(); });

function zoomBy(delta) {
  crops[current].zoom = Math.min(4, Math.max(1, crops[current].zoom + delta));
  draw();
}

// ---- output ----

const toBlob = (c, quality) => new Promise((resolve) => c.toBlob(resolve, 'image/jpeg', quality));

/** Render one platform's crop at its exact size, compressed to fit its limit. */
async function render(p) {
  const [label, w, hgt] = fmtOf(p.id);
  const { x, y, cw, ch } = region(p.id);
  let scale = 1;
  for (;;) {
    const out = document.createElement('canvas');
    out.width = Math.round(w * scale);
    out.height = Math.round(hgt * scale);
    const o = out.getContext('2d');
    o.fillStyle = '#ffffff';
    o.fillRect(0, 0, out.width, out.height);
    o.imageSmoothingQuality = 'high';
    o.drawImage(img, x, y, cw, ch, 0, 0, out.width, out.height);
    for (const q of [0.92, 0.85, 0.78, 0.7, 0.62, 0.55]) {
      const blob = await toBlob(out, q);
      if (blob && blob.size <= p.image_max_bytes) return { blob, label, width: out.width, height: out.height };
    }
    scale *= 0.85; // still too big at modest quality: make it smaller
    if (scale < 0.3) throw new Error(`Couldn't make the ${p.name} picture small enough.`);
  }
}

$('crop-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const done = $('crop-done');
  done.disabled = true;
  $('crop-out').textContent = 'Preparing pictures…';
  try {
    const result = {};
    for (const p of platforms) result[p.id] = await render(p);
    $('crop-out').textContent = Object.entries(result)
      .map(([id, r]) => `${platforms.find((p) => p.id === id).name}: ${r.width}×${r.height}, ${fmt.bytes(r.blob.size)}`)
      .join(' · ');
    const resolve = finish;
    finish = null;
    dialog.close();
    resolve(result);
  } catch (ex) {
    $('crop-out').textContent = ex.message;
  } finally {
    done.disabled = false;
  }
});
dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
dialog.addEventListener('close', () => { if (finish) { finish(null); finish = null; } });

/** Open the cropper for `src` (a same-origin picture URL) and the platforms that take pictures. */
export function openCropper(src, targets) {
  platforms = targets.filter((p) => p.image_formats?.length);
  if (!platforms.length) return Promise.resolve({});
  return new Promise((resolve, reject) => {
    const picture = new Image();
    picture.onload = () => {
      img = picture;
      crops = Object.fromEntries(platforms.map((p) => [p.id, { format: 0, zoom: 1, cx: 0.5, cy: 0.5 }]));
      current = platforms[0].id;
      finish = resolve;
      dialog.showModal();
      renderTabs();
      draw();
      canvas.focus();
    };
    picture.onerror = () => reject(new Error('That picture could not be opened for cropping.'));
    picture.src = src;
  });
}
