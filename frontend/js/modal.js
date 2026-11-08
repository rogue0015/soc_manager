// In-app questions instead of the browser's confirm() boxes: one <dialog> (#ask-dialog) for every
// "are you sure?" and every choice. ask() resolves with the chosen button's value, or null when
// closed with Esc. Destructive questions put the focus on Cancel, so Enter never deletes by accident.

import { h } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('ask-dialog');
let pending = null;   // resolves the open question

function finish(value) {
  const resolve = pending;
  pending = null;
  if (dialog.open) dialog.close();
  resolve?.(value);
}

/**
 * ask({ title, message, buttons: [{ label, value, kind: 'primary'|'danger'|'ghost', focus? }] })
 * `message` is a string, an element, or a list of them (each string becomes a paragraph).
 */
export function ask({ title, message = [], buttons }) {
  if (pending) finish(null);   // only one question at a time
  return new Promise((resolve) => {
    pending = resolve;
    $('ask-title').textContent = title;
    $('ask-message').replaceChildren(...[].concat(message).filter(Boolean)
      .map((m) => (typeof m === 'string' ? h('p', {}, m) : m)));
    const els = buttons.map((b) => h('button', {
      type: 'button', class: `btn ${{ primary: 'btn-primary', danger: 'btn-danger' }[b.kind] ?? 'btn-ghost'}`,
      onclick: () => finish(b.value),
    }, b.label));
    $('ask-buttons').replaceChildren(...els);
    dialog.showModal();
    (els[buttons.findIndex((b) => b.focus)] ?? els[0]).focus();
  });
}

/** Yes/no: resolves true only when the confirm button is pressed. */
export async function confirmAction({ title, message, confirm, danger = false }) {
  const answer = await ask({
    title, message,
    buttons: [{ label: 'Cancel', value: false, focus: danger },
      { label: confirm, value: true, kind: danger ? 'danger' : 'primary', focus: !danger }],
  });
  return answer === true;
}

dialog.addEventListener('close', () => { if (pending) finish(null); });   // Esc or the form closing
