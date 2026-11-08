// LinkedIn options in the composer: its own text, an image or a link preview,
// who can see it, reposts, and a first comment. Shown when LinkedIn is chosen.
// The values go to the API as variants.linkedin and options.linkedin; the
// backend checks them again (services/posts.py) and is the authority.

import { api } from './api.js';
import { state } from './state.js';
import { prefs } from './util.js';

const $ = (id) => document.getElementById(id);
const LI = 'linkedin';

let imageId = null;
let uploading = false;
let onChange = () => {};

const attachment = () => document.querySelector('input[name=li-attach]:checked').value;
const setAttachment = (value) => {
  document.querySelector(`input[name=li-attach][value=${value}]`).checked = true;
  showAttachment();
};

function showAttachment() {
  $('li-image-box').hidden = attachment() !== 'image';
  $('li-card-note').hidden = attachment() !== 'link_card';
}

function showImage(url) {
  const img = $('li-image-preview');
  img.hidden = !url;
  if (url) img.src = url; else img.removeAttribute('src');
}

async function uploadImage(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const status = $('li-image-status');
  uploading = true;
  status.textContent = 'Uploading…';
  try {
    const media = await api.uploadMedia(file);
    imageId = media.id;
    showImage(media.url);
    status.textContent = file.name;
    if (!$('li-image-alt').value) $('li-image-alt').focus();
  } catch (ex) {
    status.textContent = ex.message;
  } finally {
    uploading = false;
  }
}

/** The text LinkedIn will get: its own version if written, else the main text. */
export function linkedinText(mainText) {
  const own = $('li-text').value;
  return !$('li-text-field').hidden && own.trim() ? own : mainText;
}

/** Show the section when LinkedIn is chosen; its own text box only when other platforms are too. */
export function showPostOptions(platformIds) {
  const on = platformIds.includes(LI);
  $('li-options').hidden = !on;
  $('li-text-field').hidden = !on || platformIds.length < 2;
  $('li-swatch').style.background = state.byId[LI]?.color ?? '';
}

/** Fill from an existing post's LinkedIn target, or with defaults for a new post. */
export function fillPostOptions(target, { blog = false } = {}) {
  const o = target?.options ?? {};
  $('li-text').value = target && target.body_override ? target.body_override : '';
  $('li-visibility').value = o.visibility ?? prefs.get('liVisibility', 'PUBLIC');
  $('li-reshare').checked = o.allow_reshare ?? prefs.get('liReshare', true);
  $('li-comment').value = o.first_comment ?? '';
  $('li-image-alt').value = o.image_alt ?? '';
  imageId = o.image_id ?? null;
  showImage(imageId ? `/api/media/${imageId}` : null);
  $('li-image-status').textContent = imageId ? 'Image attached' : 'JPEG, PNG or GIF, up to 8 MB';
  setAttachment(o.attachment ?? (blog ? 'link_card' : 'none'));
}

/** Blog posts default to a link preview (unless something else was chosen). */
export function suggestLinkCard() {
  if (attachment() === 'none') setAttachment('link_card');
}

/** Prefill LinkedIn's own text (e.g. the full blog summary) unless the user wrote one. */
export function suggestLinkedinText(text, previous) {
  if (!$('li-text').value || $('li-text').value === previous) $('li-text').value = text;
}

/** For the API. Returns an error sentence instead when something isn't ready. */
export function readPostOptions(platformIds, mainText) {
  if (!platformIds.includes(LI)) return { variants: {}, options: {} };
  if (uploading) return { error: 'Wait for the image to finish uploading.' };
  if (attachment() === 'image' && !imageId) return { error: 'Choose an image for LinkedIn, or attach nothing.' };
  prefs.set('liVisibility', $('li-visibility').value);
  prefs.set('liReshare', $('li-reshare').checked);
  const own = linkedinText(mainText);
  return {
    variants: own !== mainText ? { [LI]: own } : {},
    options: {
      [LI]: {
        visibility: $('li-visibility').value,
        allow_reshare: $('li-reshare').checked,
        attachment: attachment(),
        image_id: attachment() === 'image' ? imageId : null,
        image_alt: $('li-image-alt').value.trim(),
        first_comment: $('li-comment').value.trim(),
      },
    },
  };
}

export function initPostOptions(changed) {
  onChange = changed;
  $('li-text').addEventListener('input', () => onChange());
  $('li-image-file').addEventListener('change', uploadImage);
  document.querySelectorAll('input[name=li-attach]').forEach((r) => r.addEventListener('change', showAttachment));
}
