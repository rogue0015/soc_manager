// "Picture or video" in the post dialog: one attachment for every chosen platform that takes it.
//
// - A picture is cropped once per platform that posts pictures (cropper.js); each crop is
//   saved to the library and sent as that platform's image_id. LinkedIn only uses it when
//   its own "Attach" choice is "Picture or video" (post-options.js).
// - A video goes as video_id to every chosen platform with the "video" feature, with the
//   YouTube title, who can see it, and tags. YouTube, TikTok and Instagram need one.
// The backend checks everything again (services/posts.py) and is the authority.

import { api } from './api.js';
import { openCropper } from './cropper.js';
import { describe, pickFromLibrary, uploadFile, videoFit } from './media.js';
import { linkedinWantsMedia, suggestLinkedinMedia } from './post-options.js';
import { state } from './state.js';
import { h } from './util.js';

const $ = (id) => document.getElementById(id);

// null | { kind: 'image', source: item, crops: {platformId: mediaId} } | { kind: 'video', item }
let media = null;
let busy = false;
let platformIds = [];
let onChange = () => {};

const takesImages = (id) => state.byId[id]?.features.includes('image');
const takesVideo = (id) => state.byId[id]?.features.includes('video');

function status(text) { $('media-status').textContent = text; }

function render() {
  $('media-remove').hidden = !media;
  $('media-recrop').hidden = media?.kind !== 'image' || !platformIds.some(takesImages);
  $('media-alt-field').hidden = !media;
  const video = media?.kind === 'video';
  const videoPlatforms = platformIds.filter(takesVideo);
  $('video-options').hidden = !video || !videoPlatforms.length;
  $('yt-title-field').hidden = !(video && platformIds.includes('youtube'));
  $('yt-tags-field').hidden = !(video && platformIds.includes('youtube'));
  $('video-privacy-field').hidden = !(video && platformIds.some((id) => ['youtube', 'tiktok'].includes(id)));

  if (!media) {
    $('media-preview').replaceChildren();
    const needs = platformIds.filter((id) => state.byId[id]?.features.includes('video_only'));
    $('media-fit').replaceChildren(...needs.map((id) =>
      h('li', { class: 'bad' }, h('span', { 'aria-hidden': 'true' }, '! '), `${state.byId[id].name} needs a video.`)));
    return;
  }
  if (video) {
    const item = media.item;
    $('media-preview').replaceChildren(h('video', { src: item.url, controls: true, preload: 'metadata', muted: true }));
    status(`${item.name || 'Video'}: ${describe(item)}`);
    // ok: true = fits, false = won't fit (fix it), null = just so you know.
    $('media-fit').replaceChildren(...videoFit(item, platformIds).map(({ p, ok, text }) => h('li',
      { class: ok ? 'ok' : ok === false ? 'bad' : 'muted' },
      ok === null ? null : h('span', { 'aria-hidden': 'true' }, ok ? '✓ ' : '! '), `${p.name} ${text}.`)));
    return;
  }
  // A picture: show each platform's crop.
  const pics = platformIds.filter(takesImages);
  $('media-preview').replaceChildren(...(pics.length ? pics : [null]).map((id) => {
    const mid = id ? (media.crops[id] ?? media.source.id) : media.source.id;
    return h('figure', { class: 'crop-thumb' },
      h('img', { src: `/api/media/${mid}`, alt: '' }),
      h('figcaption', { class: 'muted small' }, id ? `${state.byId[id].name}${media.crops[id] ? '' : ' (not cropped yet)'}` : 'Original'));
  }));
  $('media-fit').replaceChildren(...platformIds.filter((id) => !takesImages(id)).map((id) => h('li', { class: 'muted' },
    `${state.byId[id].name} ${state.byId[id].features.includes('video_only') ? 'needs a video, not a picture' : 'gets the text only'}.`)));
  status(`${media.source.name || 'Picture'}: ${describe(media.source)}`);
}

async function crop(source) {
  const targets = platformIds.filter(takesImages).map((id) => state.byId[id]);
  if (!targets.length) return {};
  const crops = await openCropper(source.url, targets);
  if (!crops) return null;
  const ids = {};
  for (const [id, c] of Object.entries(crops)) {
    status(`Saving the ${state.byId[id].name} crop…`);
    const base = (source.name || 'picture').replace(/\.[^.]+$/, '');
    ids[id] = (await api.uploadMedia(c.blob, `${base} (${state.byId[id].name} ${c.label}).jpg`)).id;
  }
  return ids;
}

async function attach(item) {
  if (item.kind === 'video') {
    media = { kind: 'video', item };
  } else {
    const crops = await crop(item);
    media = { kind: 'image', source: item, crops: crops ?? {} };
    if (crops === null) status('Cropping skipped: the original picture will be sent.');
  }
  suggestLinkedinMedia();
  render();
  onChange();
}

async function run(work) {
  if (busy) return;
  busy = true;
  $('media-options').classList.add('busy-area');
  try {
    await work();
  } catch (ex) {
    status(ex.message);
  } finally {
    busy = false;
    $('media-options').classList.remove('busy-area');
  }
}

function fromFile(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (file) run(async () => attach(await uploadFile(file, $('media-status'))));
}

// ---- used by composer.js ----

/** The chosen platforms changed: show what each will get. */
export function showMedia(ids) {
  platformIds = ids;
  render();
}

/** Prefill from an existing post (or a library item from the Media tab), or clear. */
export async function fillMedia(post, item = null) {
  media = null;
  status('');
  $('media-alt').value = '';
  $('yt-title').value = '';
  $('yt-tags').value = '';
  $('video-privacy').value = 'public';
  const targets = post?.targets ?? [];
  const withVideo = targets.find((t) => t.options?.attachment === 'video' && t.options.video_id);
  const withImage = targets.filter((t) => t.options?.attachment === 'image' && t.options.image_id);
  const o = (withVideo ?? withImage[0])?.options;
  if (o) {
    $('media-alt').value = o.image_alt ?? '';
    $('yt-title').value = o.title ?? '';
    $('yt-tags').value = o.tags ?? '';
    $('video-privacy').value = o.privacy || 'public';
  }
  try {
    if (withVideo) {
      media = { kind: 'video', item: { id: o.video_id, url: `/api/media/${o.video_id}`, kind: 'video', size: 0, used_by: [] } };
      const found = (await api.listMedia('video')).find((m) => m.id === o.video_id);
      if (found) media.item = found;
    } else if (withImage.length) {
      const crops = Object.fromEntries(withImage.map((t) => [t.platform, t.options.image_id]));
      const first = withImage[0].options.image_id;
      media = { kind: 'image', source: { id: first, url: `/api/media/${first}`, kind: 'image', size: 0 }, crops };
    } else if (item) {
      await attach(item);
      return;
    }
  } catch (ex) {
    status(ex.message);
  }
  render();
}

/** Options to merge into the post, per platform; or {error}. */
export function readMedia(ids, optionsSoFar) {
  if (busy) return { error: 'Wait for the picture or video to finish uploading.' };
  const needVideo = ids.filter((id) => state.byId[id]?.features.includes('video_only'));
  if (needVideo.length && media?.kind !== 'video') {
    return { error: `${needVideo.map((id) => state.byId[id].name).join(' and ')} only post videos. Add a video, or untick ${needVideo.length > 1 ? 'them' : 'it'}.` };
  }
  const liWants = ids.includes('linkedin') && linkedinWantsMedia() && !(optionsSoFar.linkedin ?? {}).handoff;
  if (liWants && !media) {
    return { error: 'LinkedIn is set to attach a picture or video, but none is added. Add one, or change LinkedIn’s “Attach”.' };
  }
  if (!media) return { options: {} };
  const alt = $('media-alt').value.trim();
  const out = {};
  for (const id of ids) {
    if (id === 'linkedin' && !linkedinWantsMedia()) continue;   // LinkedIn chose nothing or a link preview
    const own = optionsSoFar[id] ?? {};
    if (own.handoff) continue;                                   // LinkedIn's own scheduler: text only
    if (media.kind === 'video' && takesVideo(id)) {
      out[id] = { ...own, attachment: 'video', video_id: media.item.id, image_alt: alt,
        title: id === 'youtube' ? $('yt-title').value.trim() : '',
        tags: id === 'youtube' ? $('yt-tags').value.trim() : '',
        privacy: ['youtube', 'tiktok'].includes(id) ? $('video-privacy').value : '' };
    } else if (media.kind === 'image' && takesImages(id)) {
      out[id] = { ...own, attachment: 'image', image_id: media.crops[id] ?? media.source.id, image_alt: alt };
    }
  }
  return { options: out };
}

export function initPostMedia(changed) {
  onChange = changed;
  $('media-pic-file').addEventListener('change', fromFile);
  $('media-video-file').addEventListener('change', fromFile);
  $('media-library').addEventListener('click', () => run(async () => {
    const item = await pickFromLibrary();
    if (item) await attach(item);
  }));
  $('media-recrop').addEventListener('click', () => run(async () => {
    const crops = await crop(media.source);
    if (crops) media.crops = crops;
    render();
  }));
  $('media-remove').addEventListener('click', () => { media = null; status(''); render(); onChange(); });
}
