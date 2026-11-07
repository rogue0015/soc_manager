// Composer dialog: create a post for a day, or view/edit an existing one.

import { api } from './api.js';
import { refreshAll, state } from './state.js';
import {
  addDays, composeBlogText, dateKey, defaultTimeFor, fmt, fromInputs, h, prefs, sameDay, textLength, timeValue, toast,
} from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('composer');
const form = $('composer-form');
const body = $('post-body');
const blogPick = $('blog-pick');

let editing = null;      // the post being edited, or null when creating
let autoText = null;     // last text we generated from a blog post (so we know if the user edited it)

// ---- shared platform picker (also used by the plan dialog) ---------------

export function renderPlatformPicks(container, selected, onChange) {
  container.replaceChildren(...state.platforms.map((p) => {
    const input = h('input', { type: 'checkbox', value: p.id, checked: selected.includes(p.id), onchange: onChange });
    return h('label', {
      class: 'pick',
      style: { '--pick-color': p.color, '--pick-text': p.text_color },
      title: p.has_adapter ? p.name : `${p.name} isn't connected yet. Posts will fail until it is.`,
    },
    input,
    h('span', { class: 'swatch', style: { background: p.color } }),
    p.name,
    p.has_adapter ? null : h('span', { class: 'note' }, '(not connected)'));
  }));
}

export const pickedPlatforms = (container) =>
  [...container.querySelectorAll('input:checked')].map((i) => i.value);

// ---- helpers -------------------------------------------------------------

const selectedPlatforms = () => pickedPlatforms($('platform-picks'));
const source = () => form.querySelector('input[name=source]:checked').value;

function updateCounters() {
  const ids = selectedPlatforms();
  $('post-counters').replaceChildren(...ids.map((id) => {
    const p = state.byId[id];
    const n = textLength(p, body.value);
    return h('span', { class: `counter${n > p.char_limit ? ' over' : ''}` }, `${p.name}: ${n}/${p.char_limit}`);
  }));
}

function fillFromBlog() {
  const post = state.blog.find((b) => b.url === blogPick.value);
  if (!post) return;
  const ids = selectedPlatforms();
  const platforms = (ids.length ? ids : ['x']).map((id) => state.byId[id]);
  // Only overwrite the text if the user hasn't edited what we generated.
  if (!body.value || body.value === autoText) {
    autoText = composeBlogText(post, platforms);
    body.value = autoText;
  }
  updateCounters();
}

function setSource(value) {
  form.querySelector(`input[name=source][value=${value}]`).checked = true;
  $('blog-pick-field').hidden = value !== 'blog';
}

function renderBlogOptions(selectedUrl) {
  blogPick.replaceChildren(
    h('option', { value: '' }, state.blog.length ? 'Choose a blog post…' : 'No blog posts yet. Use Sync in the sidebar.'),
    ...state.blog.map((b) => h('option', { value: b.url, selected: b.url === selectedUrl },
      `${b.title}${b.published_on ? ` (${b.published_on})` : ''}${b.scheduled_count ? ' • scheduled' : ''}`)),
  );
}

function renderDayPosts(day) {
  const box = $('day-posts');
  const posts = state.posts.filter((p) => sameDay(new Date(p.scheduled_at), day) && p.id !== editing?.id);
  if (!posts.length) { box.replaceChildren(); return; }
  box.replaceChildren(
    h('h3', {}, `Already on ${fmt.day(day)}`),
    ...posts.map((post) => h('button', { type: 'button', class: 'day-post', onclick: () => openComposer({ post }) },
      ...post.targets.map((t) => h('span', { class: 'swatch', style: { background: state.byId[t.platform]?.color } })),
      h('span', {}, fmt.time(new Date(post.scheduled_at))),
      h('span', { class: 'text' }, post.body.split('\n')[0]))),
  );
}

function renderTargetStatus(post) {
  const box = $('target-status');
  if (!post || post.targets.every((t) => t.status === 'scheduled')) { box.replaceChildren(); return; }
  box.replaceChildren(...post.targets.map((t) => {
    const p = state.byId[t.platform];
    return h('div', { class: 'target-line' },
      h('span', { class: 'swatch', style: { background: p?.color } }),
      h('strong', {}, p?.name ?? t.platform),
      h('span', {}, t.status),
      t.external_url ? h('a', { href: t.external_url, target: '_blank', rel: 'noopener' }, 'View post') : null,
      t.error ? h('span', { class: 'err' }, t.error) : null);
  }));
}

function setReadOnly(readOnly) {
  for (const el of $('composer-fields').querySelectorAll('input, textarea, select')) el.disabled = readOnly;
  $('post-save').hidden = readOnly;
}

// ---- open ----------------------------------------------------------------

/**
 * openComposer({ date })          new post on a day
 * openComposer({ post })          view / edit an existing post
 * openComposer({ blog, date? })   new post prefilled from a blog post
 */
export function openComposer({ date, post, blog } = {}) {
  editing = post ?? null;
  autoText = null;
  $('composer-error').textContent = '';

  const when = post ? new Date(post.scheduled_at) : null;
  const day = when ?? date ?? addDays(new Date(), 1);
  const published = post?.targets.some((t) => t.status === 'published' || t.status === 'publishing');

  $('composer-title').textContent = post ? (published ? 'Published post' : 'Edit post') : `Schedule for ${fmt.day(day)}`;
  $('post-save').textContent = post ? 'Save changes' : 'Schedule';
  $('post-delete').hidden = !post;
  $('post-retry').hidden = !post?.targets.some((t) => t.status === 'failed');

  $('post-date').value = dateKey(day);
  $('post-date').min = dateKey(new Date());
  $('post-time').value = when ? timeValue(when) : defaultTimeFor(day);

  const platforms = post ? post.targets.map((t) => t.platform) : prefs.get('lastPlatforms', ['x']);
  renderPlatformPicks($('platform-picks'), platforms, () => { prefs.set('lastPlatforms', selectedPlatforms()); fillFromBlog(); updateCounters(); });

  const blogUrl = post?.blog_url ?? blog?.url ?? '';
  renderBlogOptions(blogUrl);
  setSource(blogUrl ? 'blog' : 'custom');
  body.value = post?.body ?? '';
  if (blog) fillFromBlog();

  renderDayPosts(day);
  renderTargetStatus(post);
  setReadOnly(Boolean(published));
  updateCounters();

  if (!dialog.open) dialog.showModal();
  (published ? $('post-delete') : body).focus();
}

// ---- events --------------------------------------------------------------

body.addEventListener('input', updateCounters);
blogPick.addEventListener('change', () => { body.value = ''; fillFromBlog(); });
form.querySelectorAll('input[name=source]').forEach((r) => r.addEventListener('change', () => {
  setSource(source());
  if (source() === 'blog') blogPick.focus();
}));
dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('composer-error');
  err.textContent = '';
  const platforms = selectedPlatforms();
  const { value: date } = $('post-date');
  const { value: time } = $('post-time');
  if (!body.value.trim()) return void (err.textContent = 'Write something to post.');
  if (!platforms.length) return void (err.textContent = 'Choose at least one platform.');
  if (!date || !time) return void (err.textContent = 'Pick a date and time.');

  const payload = {
    body: body.value,
    scheduled_at: fromInputs(date, time).toISOString(),
    platforms,
    blog_url: source() === 'blog' && blogPick.value ? blogPick.value : null,
  };
  const save = $('post-save');
  save.disabled = true;
  try {
    if (editing) await api.updatePost(editing.id, payload);
    else await api.createPost(payload);
    dialog.close();
    toast(editing ? 'Post updated' : `Scheduled for ${fmt.shortDate(fromInputs(date, time))} at ${time}`);
    await refreshAll();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    save.disabled = false;
  }
});

$('post-delete').addEventListener('click', async () => {
  const published = editing?.targets.some((t) => t.status === 'published');
  const msg = published
    ? 'Remove this post from the planner? It stays live on the platforms; delete it there if needed.'
    : 'Delete this scheduled post?';
  if (!editing || !confirm(msg)) return;
  try {
    await api.deletePost(editing.id);
    dialog.close();
    toast('Post removed');
    await refreshAll();
  } catch (ex) {
    $('composer-error').textContent = ex.message;
  }
});

$('post-retry').addEventListener('click', async () => {
  if (!editing) return;
  try {
    await api.retryPost(editing.id);
    dialog.close();
    toast('Queued for retry. It will go out within a minute if its time has passed.');
    await refreshAll();
  } catch (ex) {
    $('composer-error').textContent = ex.message;
  }
});
