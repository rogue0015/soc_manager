// LinkedIn options in the composer: who posts it (this app, or LinkedIn's own scheduler),
// its own text, an image or a link preview, who can see it, reposts, and a first comment.
// Shown when LinkedIn is chosen. When LinkedIn's scheduler posts it, only the text goes
// over (js/handoff.js), so the other options are hidden. The values go to the API as
// variants.linkedin and options.linkedin; the backend checks them again (services/posts.py).

import { state } from './state.js';
import { prefs } from './util.js';

const $ = (id) => document.getElementById(id);
const LI = 'linkedin';

let onChange = () => {};

const attachment = () => document.querySelector('input[name=li-attach]:checked').value;
/** True when LinkedIn's own scheduler posts it (the app only hands the text over). */
export const isHandoff = () => document.querySelector('input[name=li-sender]:checked').value === 'linkedin';

function showSender() {
  $('li-app-options').hidden = isHandoff();
  $('li-handoff-note').hidden = !isHandoff();
}

function setSender(value) {
  document.querySelector(`input[name=li-sender][value=${value}]`).checked = true;
  showSender();
}
const setAttachment = (value) => {
  document.querySelector(`input[name=li-attach][value=${value}]`).checked = true;
  showAttachment();
};

function showAttachment() {
  $('li-card-note').hidden = attachment() !== 'link_card';
  $('li-media-note').hidden = attachment() !== 'image';
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
  // "image" here means "the picture or video from the shared section", whichever it is.
  setAttachment(o.attachment === 'video' ? 'image' : (o.attachment ?? (blog ? 'link_card' : 'none')));
  setSender((target ? o.handoff : prefs.get('liHandoff', false)) ? 'linkedin' : 'app');
}

/** A picture or video was added: LinkedIn uses it unless something else was chosen on purpose. */
export function suggestLinkedinMedia() {
  if (attachment() === 'none' && !isHandoff()) setAttachment('image');
}

/** Does LinkedIn want the shared picture or video? */
export const linkedinWantsMedia = () => !isHandoff() && attachment() === 'image';

/** Blog posts default to a link preview (unless something else was chosen). */
export function suggestLinkCard() {
  if (attachment() === 'none' && !isHandoff()) setAttachment('link_card');
}

/** Prefill LinkedIn's own text (e.g. the full blog summary) unless the user wrote one. */
export function suggestLinkedinText(text, previous) {
  if (!$('li-text').value || $('li-text').value === previous) $('li-text').value = text;
}

/** For the API. Returns an error sentence instead when something isn't ready. */
export function readPostOptions(platformIds, mainText) {
  if (!platformIds.includes(LI)) return { variants: {}, options: {} };
  const own = linkedinText(mainText);
  const variants = own !== mainText ? { [LI]: own } : {};
  prefs.set('liHandoff', isHandoff());
  if (isHandoff()) return { variants, options: { [LI]: { handoff: true } } };
  prefs.set('liVisibility', $('li-visibility').value);
  prefs.set('liReshare', $('li-reshare').checked);
  return {
    variants,
    options: {
      [LI]: {
        visibility: $('li-visibility').value,
        allow_reshare: $('li-reshare').checked,
        // "image" = use the shared picture or video; composer.js fills in which (post-media.js).
        attachment: attachment(),
        first_comment: $('li-comment').value.trim(),
      },
    },
  };
}

export function initPostOptions(changed) {
  onChange = changed;
  $('li-text').addEventListener('input', () => onChange());
  document.querySelectorAll('input[name=li-attach]').forEach((r) => r.addEventListener('change', showAttachment));
  document.querySelectorAll('input[name=li-sender]').forEach((r) => r.addEventListener('change', showSender));
}
