// AI generation tab: Gemini writes one draft per platform from an idea or a blog post (plus an
// optional event recap), in a persona's style. You review and edit each draft, then open it in
// the post window to schedule or post it yourself: nothing is posted from here. Every
// generation is kept in the history. Loads when the tab is shown; no polling. See ADR 0012.

import { aiApi } from './ai-api.js';
import { openComposer } from './composer.js';
import { initPersonas, openPersonas } from './personas.js';
import { refreshBlog, state } from './state.js';
import { onShow } from './tabs.js';
import { fmt, h, prefs, textLength, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const edits = new Map();   // "generationId:platform" -> your edited text, while the page is open

let personas = [];
let history = [];
let shown = null;          // the generation whose drafts are on screen

/** Platforms that take written text. dev.to publishes the whole blog post instead. */
const writable = () => state.platforms.filter((p) => !p.features.includes('article'));
const source = () => document.querySelector('input[name=ai-source]:checked').value;
const picked = () => [...$('ai-platforms').querySelectorAll('input:checked')].map((i) => i.value);
const nameOf = (id) => state.byId[id]?.name ?? id;

// ---- the form ------------------------------------------------------------------------------

function syncAll() {
  const boxes = [...$('ai-platforms').querySelectorAll('input')];
  $('ai-all').checked = boxes.length > 0 && boxes.every((i) => i.checked);
}

function renderPlatforms() {
  const chosen = new Set(picked().length ? picked() : prefs.get('aiPlatforms', ['linkedin', 'x']));
  $('ai-platforms').replaceChildren(...writable().map((p) => h('label', {
    class: 'pick', style: { '--pick-color': p.color, '--pick-text': p.text_color }, title: p.name,
  },
  h('input', { type: 'checkbox', value: p.id, checked: chosen.has(p.id), onchange: syncAll }),
  h('span', { class: 'swatch', style: { background: p.color } }),
  p.name)));
  syncAll();
}

function renderPersonas(list) {
  personas = list;
  const select = $('ai-persona');
  const wanted = Number(select.value) || prefs.get('aiPersona', null);
  select.replaceChildren(...(list.length
    ? list.map((p) => h('option', { value: p.id }, p.name))
    : [h('option', { value: '' }, 'No personas yet: press Manage personas')]));
  if (list.some((p) => p.id === wanted)) select.value = String(wanted);
}

function renderBlogs() {
  const select = $('ai-blog');
  const keep = select.value;
  select.replaceChildren(
    h('option', { value: '' }, state.blog.length ? 'Choose a blog post…' : 'No blog posts yet: press Sync on the Scheduler tab'),
    ...state.blog.map((b) => h('option', { value: b.url }, `${b.title}${b.published_on ? ` (${b.published_on})` : ''}`)));
  select.value = keep;
}

function setSource(value) {
  $('ai-blog-field').hidden = value !== 'blog';
  $('ai-idea-label').textContent = value === 'blog' ? 'What you want to say about it (optional)' : 'The idea';
}

async function renderUsage() {
  try {
    const s = await aiApi.status();
    $('ai-usage').textContent = s.ready
      ? `${s.model} · ${fmt.num(s.used_today)} of ${fmt.num(s.per_day)} requests used today`
      : s.message;
    $('ai-generate').disabled = !s.ready;
  } catch {
    $('ai-usage').textContent = '';
  }
}

function readRequest() {
  const platforms = picked();
  const personaId = Number($('ai-persona').value);
  const req = {
    source: source(),
    idea: $('ai-idea').value.trim(),
    blog_url: source() === 'blog' ? $('ai-blog').value || null : null,
    event: {
      name: $('ai-event-name').value.trim(), day: $('ai-event-day').value.trim(),
      venue: $('ai-event-venue').value.trim(), takeaways: $('ai-event-takeaways').value.trim(),
    },
    platforms,
    persona_id: personaId,
  };
  if (req.source === 'idea' && !req.idea) return { error: 'Write the idea for the post first.' };
  if (req.source === 'blog' && !req.blog_url) return { error: 'Pick a blog post to write about.' };
  if (!platforms.length) return { error: 'Choose at least one platform.' };
  if (!personaId) return { error: 'Pick a persona. Manage personas → Add starter personas gives you two to start with.' };
  return { req };
}

async function generate(e) {
  e.preventDefault();
  const err = $('ai-error');
  err.textContent = '';
  const { req, error } = readRequest();
  if (error) return void (err.textContent = error);
  prefs.set('aiPlatforms', req.platforms);
  prefs.set('aiPersona', req.persona_id);
  const button = $('ai-generate');
  button.disabled = true;
  button.textContent = 'Writing drafts…';
  $('ai-drafts').setAttribute('aria-busy', 'true');
  try {
    shown = await aiApi.generate(req);
    renderDrafts();
    toast(`Drafts ready for ${fmt.count(req.platforms.length, 'platform')}`);
    await loadHistory();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    button.textContent = 'Generate drafts';
    $('ai-drafts').removeAttribute('aria-busy');
    await renderUsage();   // also re-enables the button when Gemini is ready
  }
}

// ---- drafts ------------------------------------------------------------------------------------

function summary(gen) {
  if (gen.source === 'blog') {
    const blog = state.blog.find((b) => b.url === gen.blog_url);
    return `Blog post: ${blog?.title ?? gen.blog_url}`;
  }
  return gen.idea.split('\n')[0];
}

function counter(p, text, limit = p.char_limit) {
  const n = textLength(p, text);
  return { text: `${fmt.num(n)}/${fmt.num(limit)}`, over: n > limit };
}

function markUsed(gen, platform, post) {
  aiApi.markUsed(gen.id, platform, post.id).then((updated) => {
    if (shown?.id === updated.id) {
      shown = updated;
      renderDrafts();
    }
    loadHistory();
  }).catch(() => {});   // the post is saved either way; only the history note is missing
}

function draftCard(gen, pid) {
  const p = state.byId[pid] ?? { id: pid, name: pid, color: 'var(--muted)', char_limit: 0, features: [] };
  const d = gen.drafts[pid];
  const key = `${gen.id}:${pid}`;
  const count = h('span', { class: 'counter' });
  const text = h('textarea', { rows: Math.min(12, Math.max(3, Math.ceil((d.text || '').length / 60))),
    'aria-label': `${p.name} draft` });
  text.value = edits.get(key) ?? d.text;
  const update = () => {
    const c = counter(p, text.value);
    count.textContent = c.text;
    count.classList.toggle('over', c.over);
    if (text.value !== d.text) edits.set(key, text.value); else edits.delete(key);
  };
  text.addEventListener('input', update);
  update();

  const title = p.title_limit != null && d.title != null
    ? h('label', { class: 'field' }, h('span', {}, `Title (${p.name}, up to ${p.title_limit} characters)`),
      h('input', { type: 'text', value: d.title, readonly: true }))
    : null;

  const regen = h('button', { class: 'btn btn-ghost btn-sm', type: 'button' }, 'Regenerate');
  regen.addEventListener('click', async () => {
    regen.disabled = true;
    regen.textContent = 'Writing…';
    $('ai-error').textContent = '';
    try {
      shown = await aiApi.regenerate(gen.id, pid);
      edits.delete(key);
      renderDrafts();
      toast(`New ${p.name} draft`);
    } catch (ex) {
      $('ai-error').textContent = ex.message;
      regen.disabled = false;
      regen.textContent = 'Regenerate';
    }
    renderUsage();
  });

  const actions = d.error && !d.text ? [regen] : [
    h('button', {
      class: 'btn btn-ghost btn-sm', type: 'button',
      onclick: () => openComposer({
        platform: pid, text: text.value, blog: gen.blog_url ? { url: gen.blog_url } : undefined,
        onSaved: (post) => markUsed(gen, pid, post),
      }),
    }, 'Open in post window'),
    h('button', {
      class: 'btn btn-ghost btn-sm', type: 'button',
      onclick: () => navigator.clipboard?.writeText(text.value).then(() => toast('Copied'),
        () => toast('Couldn’t copy. Select the text and press Ctrl+C.', { error: true })),
    }, 'Copy'),
    regen,
  ];

  return h('article', { class: 'ai-draft', 'aria-label': `${p.name} draft` },
    h('div', { class: 'ai-draft-head' },
      h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' }),
      h('strong', {}, p.name),
      d.post_id ? h('span', { class: 'state ok' }, h('span', { 'aria-hidden': 'true' }, '✓ '), 'saved as a post') : null,
      count),
    d.error ? h('p', { class: 'err small' }, d.error) : null,
    d.over ? h('p', { class: 'warn-text small' }, `Still too long for ${p.name} after a second try: shorten it before posting.`) : null,
    d.unknown_links?.length ? h('p', { class: 'warn-text small' }, `Has a link you didn’t give: ${d.unknown_links.join(', ')}. `
      + 'Remove or replace it before posting.') : null,
    d.title_missing ? h('p', { class: 'warn-text small' }, `No title yet: press Regenerate, or write one in the post window.`) : null,
    title,
    d.error && !d.text ? null : text,
    p.features.includes('video_only') ? h('p', { class: 'muted small' }, `${p.name} needs a video: add it in the post window.`) : null,
    h('div', { class: 'row wrap' }, actions));
}

function renderDrafts() {
  const box = $('ai-drafts');
  if (!shown) {
    $('ai-drafts-meta').textContent = '';
    box.replaceChildren(h('p', { class: 'empty' }, 'Your drafts appear here. Fill in the form and press Generate drafts.'));
    return;
  }
  $('ai-drafts-meta').textContent = `${shown.persona.name} · ${fmt.dateTime(new Date(shown.created_at))} · ${summary(shown)}`;
  box.replaceChildren(...shown.platforms.map((pid) => draftCard(shown, pid)));
  for (const li of $('ai-history').children) {
    if (Number(li.dataset.id) === shown.id) li.setAttribute('aria-current', 'true');
    else li.removeAttribute('aria-current');
  }
}

// ---- history ---------------------------------------------------------------------------------

function renderHistory() {
  const list = $('ai-history');
  if (!history.length) {
    list.replaceChildren(h('li', { class: 'empty' }, 'Nothing generated yet.'));
    return;
  }
  list.replaceChildren(...history.map((gen) => {
    const used = Object.values(gen.drafts).filter((d) => d.post_id).length;
    const li = h('li', { 'data-id': gen.id, 'aria-current': shown?.id === gen.id ? 'true' : null },
      h('button', {
        type: 'button', class: 'ai-history-open', title: 'Show these drafts',
        onclick: () => { shown = gen; renderDrafts(); $('ai-drafts-title').scrollIntoView({ block: 'start', behavior: 'smooth' }); },
      },
      h('strong', {}, summary(gen)),
      h('span', { class: 'meta' }, [fmt.when(new Date(gen.created_at)), gen.persona.name,
        gen.platforms.map(nameOf).join(', '), used ? `${used} posted` : null].filter(Boolean).join(' · '))),
      h('button', {
        type: 'button', class: 'icon-btn small-icon', 'aria-label': `Delete this generation (${summary(gen)})`,
        onclick: async () => {
          if (!confirm('Delete these drafts from the history? Posts made from them stay.')) return;
          try {
            await aiApi.deleteGeneration(gen.id);
            if (shown?.id === gen.id) { shown = null; renderDrafts(); }
            await loadHistory();
          } catch (ex) {
            $('ai-error').textContent = ex.message;
          }
        },
      }, '×'));
    return li;
  }));
}

async function loadHistory() {
  try {
    history = await aiApi.history();
  } catch (ex) {
    $('ai-error').textContent = ex.message;
  }
  renderHistory();
}

// ---- start -----------------------------------------------------------------------------------

async function onShowAI() {
  renderPlatforms();
  renderBlogs();
  if (!state.blog.length) refreshBlog().then(renderBlogs);
  try {
    renderPersonas(await aiApi.personas());
  } catch (ex) {
    $('ai-error').textContent = ex.message;
  }
  await Promise.all([loadHistory(), renderUsage()]);
  if (!shown && history.length) shown = history[0];   // pick up where you left off
  renderDrafts();
}

export function initAI() {
  initPersonas();
  $('ai-form').addEventListener('submit', generate);
  document.querySelectorAll('input[name=ai-source]').forEach((r) => r.addEventListener('change', () => setSource(source())));
  $('ai-all').addEventListener('change', (e) => {
    for (const box of $('ai-platforms').querySelectorAll('input')) box.checked = e.target.checked;
  });
  $('ai-personas-btn').addEventListener('click', () =>
    openPersonas(renderPersonas, Number($('ai-persona').value) || null));
  onShow('ai', onShowAI);
}
