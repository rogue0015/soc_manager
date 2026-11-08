// "Finish in LinkedIn": LinkedIn's API can only post straight away, so a post meant for
// LinkedIn's own scheduler is handed over: the app copies the text and opens LinkedIn's
// post box (compose_url from the registry), and you set the time there. See ADR 0008.

import { state } from './state.js';
import { fmt, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('handoff-dialog');
let current = null;   // { platform, text, when }

/** Show the steps for handing `text` to `platform`'s own scheduler, to go out at `when` (a Date). */
export function openHandoff({ platform, text, when }) {
  const p = state.byId[platform];
  if (!p?.compose_url) return;
  current = { p, text, when };
  $('handoff-title').textContent = `Finish in ${p.name}`;
  $('handoff-name').textContent = p.name;
  for (const el of dialog.querySelectorAll('.handoff-platform')) el.textContent = p.name;
  $('handoff-open').textContent = `Copy text and open ${p.name}`;
  $('handoff-when').textContent = fmt.dateTime(when);
  $('handoff-text').value = text;
  $('handoff-result').className = 'result';
  $('handoff-result').textContent = '';
  if (!dialog.open) dialog.showModal();
  $('handoff-open').focus();
}

function copyText(text) {
  // The Clipboard API needs a secure page (localhost counts); fall back to selecting the text box.
  return navigator.clipboard?.writeText(text).then(() => true, () => false) ?? Promise.resolve(false);
}

async function openPlatform() {
  const { p, text, when } = current;
  const copying = copyText(text);
  // Open in the click itself: browsers block windows opened after an await.
  window.open(p.compose_url.replace('{text}', encodeURIComponent(text)), '_blank', 'noopener');
  const copied = await copying;
  const result = $('handoff-result');
  if (copied) {
    result.className = 'result ok';
    result.textContent = `Text copied. ${p.name} opened in a new tab: paste it if the box is empty, `
      + `then schedule it for ${fmt.dateTime(when)}.`;
    toast(`Text copied. Schedule it in ${p.name}.`);
  } else {
    $('handoff-text').select();
    result.className = 'result';
    result.textContent = `${p.name} opened in a new tab. Copy the selected text above (Ctrl+C), then paste it there.`;
  }
}

export function initHandoff() {
  $('handoff-open').addEventListener('click', openPlatform);
  dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
}
