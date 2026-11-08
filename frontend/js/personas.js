// Personas dialog (AI generation tab): your personas on the left, the chosen one's fields on
// the right. A persona says how posts sound (tone, voice, format, ...); it never sets lengths
// or platforms, so any persona works with any platform. The backend checks every field again.

import { aiApi } from './ai-api.js';
import { h, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('persona-dialog');
const FIELDS = ['tone', 'voice', 'language', 'format', 'hashtags', 'emoji', 'always', 'never', 'example'];

let personas = [];
let current = null;          // id of the persona in the form; null while writing a new one
let onChange = () => {};     // tells the AI tab the list changed

function renderList() {
  $('persona-list').replaceChildren(...(personas.length
    ? personas.map((p) => h('li', {}, h('button', {
      type: 'button', class: 'persona-item', 'aria-current': p.id === current ? 'true' : null, onclick: () => fill(p),
    }, p.name)))
    : [h('li', { class: 'muted small' }, 'No personas yet. Add the starter ones, or make your own.')]));
}

function fill(persona) {
  current = persona?.id ?? null;
  $('persona-name').value = persona?.name ?? '';
  for (const f of FIELDS) $(`persona-${f}`).value = persona?.[f] ?? '';
  $('persona-delete').hidden = current == null;
  $('persona-duplicate').hidden = current == null;
  $('persona-error').textContent = '';
  renderList();
}

const read = () => ({
  name: $('persona-name').value.trim(),
  ...Object.fromEntries(FIELDS.map((f) => [f, $(`persona-${f}`).value.trim()])),
});

async function reload(selectId = current) {
  personas = await aiApi.personas();
  onChange(personas);
  fill(personas.find((p) => p.id === selectId) ?? personas[0] ?? null);
}

/** Open the dialog. `changed(list)` runs whenever personas are saved, added or deleted. */
export async function openPersonas(changed, selectId) {
  onChange = changed;
  try {
    personas = await aiApi.personas();
  } catch (ex) {
    toast(ex.message, { error: true });
    return;
  }
  fill(personas.find((p) => p.id === selectId) ?? personas[0] ?? null);
  if (!dialog.open) dialog.showModal();
  $('persona-name').focus();
}

async function work(button, job) {
  const err = $('persona-error');
  err.textContent = '';
  button.disabled = true;
  try {
    await job();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    button.disabled = false;
  }
}

export function initPersonas() {
  $('persona-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const data = read();
    if (!data.name) return void ($('persona-error').textContent = 'Give the persona a name.');
    work($('persona-save'), async () => {
      const saved = current == null ? await aiApi.createPersona(data) : await aiApi.updatePersona(current, data);
      toast(`Saved “${saved.name}”`);
      await reload(saved.id);
    });
  });
  $('persona-new').addEventListener('click', () => {
    fill(null);
    $('persona-name').focus();
  });
  $('persona-duplicate').addEventListener('click', () => {
    const copy = read();
    fill(null);   // a new persona with the same fields
    for (const f of FIELDS) $(`persona-${f}`).value = copy[f];
    $('persona-name').value = `${copy.name} copy`;
    $('persona-name').select();
  });
  $('persona-delete').addEventListener('click', (e) => {
    const name = $('persona-name').value || 'this persona';
    if (!confirm(`Delete the persona “${name}”? Drafts already written with it keep their own copy.`)) return;
    work(e.currentTarget, async () => {
      await aiApi.deletePersona(current);
      toast(`Deleted “${name}”`);
      current = null;
      await reload(null);
    });
  });
  $('persona-starters').addEventListener('click', (e) => work(e.currentTarget, async () => {
    personas = await aiApi.addStarters();
    onChange(personas);
    toast('Starter personas added');
    fill(personas.find((p) => p.id === current) ?? personas[0] ?? null);
  }));
  dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));
}
