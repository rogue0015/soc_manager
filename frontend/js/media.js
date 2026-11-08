// Media tab: the library of pictures and videos, plus the "From library…" picker the
// post dialog uses. Pictures can be cropped into copies for any platform (cropper.js).

import { api } from './api.js';
import { openCropper } from './cropper.js';
import { state } from './state.js';
import { onShow } from './tabs.js';
import { announcePostsChanged, fmt, h, toast } from './util.js';

const $ = (id) => document.getElementById(id);
let items = [];
let openComposerFn = null;   // set by main.js (composer imports this module, so no import cycle)

// ---- describing an item ----

export const duration = (ms) => {
  if (ms == null) return '';
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

export function describe(item) {
  const size = item.width && item.height ? `${item.width}×${item.height}` : 'size unknown';
  if (item.kind === 'video') {
    const shape = !item.width ? '' : item.height > item.width ? ' (vertical)' : item.height === item.width ? ' (square)' : ' (landscape)';
    return `Video · ${duration(item.duration_ms) || 'length unknown'} · ${size}${shape} · ${fmt.bytes(item.size)}`;
  }
  return `Picture · ${size} · ${fmt.bytes(item.size)}`;
}

/** One line per chosen platform: will this video work there, and how? Limits come from the registry. */
export function videoFit(item, platformIds) {
  return platformIds.map((id) => {
    const p = state.byId[id];
    if (!p) return null;
    if (!p.features.includes('video')) return { p, ok: null, text: 'gets the text only (no video from this app)' };
    const ms = item.duration_ms;
    if (p.video_max_bytes && item.size > p.video_max_bytes)
      return { p, ok: false, text: `takes videos up to ${fmt.bytes(p.video_max_bytes)}` };
    if (ms != null && p.video_max_ms && ms > p.video_max_ms)
      return { p, ok: false, text: `takes videos up to ${duration(p.video_max_ms)} long` };
    if (ms != null && p.video_min_ms && ms < p.video_min_ms)
      return { p, ok: false, text: `needs at least ${p.video_min_ms / 1000} seconds` };
    if (id === 'youtube') {
      const short = item.height > item.width && ms != null && ms <= 180_000;
      return { p, ok: true, text: short ? 'becomes a Short (vertical, up to 3 min)' : 'a regular video' };
    }
    if (id === 'instagram') return { p, ok: true, text: 'a Reel' };
    return { p, ok: true, text: 'posts the video' };
  }).filter(Boolean);
}

export async function uploadFile(file, status) {
  status.textContent = `Uploading ${file.name}…`;
  return api.uploadMedia(file, file.name, (share) => {
    status.textContent = `Uploading ${file.name}… ${Math.round(share * 100)}%`;
  });
}

function thumb(item) {
  return item.kind === 'video'
    ? h('video', { src: `${item.url}#t=0.5`, preload: 'metadata', muted: true, playsinline: true, controls: true,
      'aria-label': item.name || 'Video' })
    : h('img', { src: item.url, alt: item.name || 'Picture', loading: 'lazy' });
}

// ---- the Media tab ----

const imagePlatforms = () => state.platforms.filter((p) => p.image_formats?.length);

async function cropCopies(item) {
  const result = $('lib-result');
  try {
    const crops = await openCropper(item.url, imagePlatforms());
    if (!crops) return;
    result.className = 'result';
    result.textContent = 'Saving copies…';
    const base = (item.name || 'picture').replace(/\.[^.]+$/, '');
    for (const [id, c] of Object.entries(crops)) {
      await api.uploadMedia(c.blob, `${base} (${state.byId[id].name} ${c.label}).jpg`);
    }
    result.className = 'result ok';
    const n = Object.keys(crops).length;
    result.textContent = `Saved ${n} ${n === 1 ? 'copy' : 'copies'}. Download one with its ⤓ link to post it by hand.`;
    await loadLibrary();
  } catch (ex) {
    result.className = 'result bad';
    result.textContent = ex.message;
  }
}

async function remove(item) {
  if (!confirm(`Delete “${item.name || 'this file'}” from the library? Posts already published keep it.`)) return;
  try {
    await api.deleteMedia(item.id);
    toast('Deleted from the library');
    await loadLibrary();
  } catch (ex) {
    $('lib-result').className = 'result bad';
    $('lib-result').textContent = ex.message;
  }
}

function card(item) {
  const used = item.used_by.length;
  const usedText = used
    ? `Used in ${fmt.count(new Set(item.used_by.map((u) => u.post_id)).size, 'post')} (${[...new Set(item.used_by.map((u) => state.byId[u.platform]?.name ?? u.platform))].join(', ')})`
    : 'Not used yet';
  return h('li', { class: 'media-card' },
    h('div', { class: 'media-thumb' }, thumb(item)),
    h('div', { class: 'media-info' },
      h('strong', { class: 'media-name', title: item.name || '' }, item.name || 'Untitled'),
      h('span', { class: 'muted small' }, describe(item)),
      h('span', { class: 'muted small' }, usedText)),
    h('div', { class: 'row wrap media-actions' },
      h('button', { class: 'btn btn-primary btn-sm', type: 'button',
        onclick: () => openComposerFn?.({ media: item }) }, 'New post with this'),
      item.kind === 'image'
        ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => cropCopies(item) }, 'Crop a copy…')
        : null,
      h('a', { class: 'btn btn-ghost btn-sm', href: item.url, download: item.name || '', title: 'Download' }, '⤓'),
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', 'aria-label': `Delete ${item.name || 'file'}`,
        onclick: () => remove(item) }, 'Delete')));
}

async function loadLibrary() {
  const kind = document.querySelector('input[name=lib-kind]:checked').value;
  try {
    items = await api.listMedia(kind);
    $('lib-grid').replaceChildren(...(items.length ? items.map(card)
      : [h('li', { class: 'empty' }, kind === 'video' ? 'No videos yet. Add one above.'
        : kind === 'image' ? 'No pictures yet. Add one above.' : 'Nothing here yet. Add a picture or video above.')]));
  } catch (ex) {
    $('lib-result').className = 'result bad';
    $('lib-result').textContent = ex.message;
  }
}

async function addFromTab(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const result = $('lib-result');
  result.className = 'result';
  const label = e.target.closest('label');
  label.classList.add('busy');
  try {
    const item = await uploadFile(file, result);
    result.className = 'result ok';
    result.textContent = `Added ${item.name}: ${describe(item)}.`;
    announcePostsChanged();
    await loadLibrary();
  } catch (ex) {
    result.className = 'result bad';
    result.textContent = ex.message;
  } finally {
    label.classList.remove('busy');
  }
}

// ---- the picker used by the post dialog ----

/** Let the user choose a library item. Resolves with the item, or null. */
export async function pickFromLibrary() {
  const dialog = $('library-dialog');
  const list = await api.listMedia();
  return new Promise((resolve) => {
    let chosen = null;
    $('library-pick').replaceChildren(...(list.length ? list.map((item) => h('li', { class: 'media-card' },
      h('div', { class: 'media-thumb' }, thumb(item)),
      h('div', { class: 'media-info' },
        h('strong', { class: 'media-name' }, item.name || 'Untitled'),
        h('span', { class: 'muted small' }, describe(item))),
      h('button', { class: 'btn btn-primary btn-sm', type: 'button',
        onclick: () => { chosen = item; dialog.close(); } }, 'Use this')))
      : [h('li', { class: 'empty' }, 'The library is empty. Use “Add picture…” or “Add video…” instead.')]));
    dialog.addEventListener('close', () => resolve(chosen), { once: true });
    dialog.showModal();
  });
}

export function initMedia(openComposer) {
  openComposerFn = openComposer;
  $('lib-pic-file').addEventListener('change', addFromTab);
  $('lib-video-file').addEventListener('change', addFromTab);
  document.querySelectorAll('input[name=lib-kind]').forEach((r) => r.addEventListener('change', loadLibrary));
  $('library-dialog').querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => $('library-dialog').close()));
  onShow('media', loadLibrary);
  window.addEventListener('sm:posts-changed', () => { if (document.getElementById('panel-media').hidden === false) loadLibrary(); });
}
