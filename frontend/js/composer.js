// Composer dialog: create a post for a day, or view/edit an existing one.

import { api } from './api.js';
import { renderTargets } from './live.js';
import {
  fillPostOptions, initPostOptions, linkedinText, readPostOptions, showPostOptions, suggestLinkCard, suggestLinkedinText,
} from './post-options.js';
import { refreshAll, state } from './state.js';
import {
  addDays, announcePostsChanged, composeBlogText, dateKey, defaultTimeFor, fmt, fromInputs, h, prefs, sameDay,
  textLength, timeValue, toast,
} from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('composer');
const form = $('composer-form');
const body = $('post-body');
const blogPick = $('blog-pick');

let editing = null;      // the post being edited, or null when creating
let autoText = null;     // last text we generated from a blog post (so we know if the user edited it)
let autoLiText = null;   // same, for LinkedIn's own version

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
    const n = textLength(p, id === 'linkedin' ? linkedinText(body.value) : body.value);
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
  // LinkedIn has room for the whole summary when a stricter platform shortened the main text.
  if (ids.includes('linkedin') && ids.length > 1) {
    const full = composeBlogText(post, [state.byId.linkedin]);
    suggestLinkedinText(full === body.value ? '' : full, autoLiText);
    autoLiText = full === body.value ? '' : full;
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
  // After a live edit or delete: show the new state and refresh the calendar behind the dialog.
  renderTargets(post, (updated) => {
    editing = updated;
    renderTargetStatus(updated);
    refreshAll();
    announcePostsChanged();
  });
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
  autoLiText = null;
  $('composer-error').textContent = '';

  const when = post ? new Date(post.scheduled_at) : null;
  const day = when ?? date ?? addDays(new Date(), 1);
  const published = post?.targets.some((t) => t.status === 'published' || t.status === 'publishing');

  $('composer-title').textContent = post ? (published ? 'Published post' : 'Edit post') : `Schedule for ${fmt.day(day)}`;
  $('post-save').textContent = post ? 'Save changes' : 'Schedule';
  $('post-delete').hidden = !post;
  // On a live post, "Delete" would read like deleting it on LinkedIn; this button only touches the planner.
  $('post-delete').textContent = published ? 'Remove from planner' : 'Delete';
  $('post-retry').hidden = !post?.targets.some((t) => t.status === 'failed');

  $('post-date').value = dateKey(day);
  $('post-date').min = dateKey(new Date());
  $('post-time').value = when ? timeValue(when) : defaultTimeFor(day);

  const platforms = post ? post.targets.map((t) => t.platform) : prefs.get('lastPlatforms', ['x']);
  renderPlatformPicks($('platform-picks'), platforms, () => {
    prefs.set('lastPlatforms', selectedPlatforms());
    showPostOptions(selectedPlatforms());
    fillFromBlog();
    updateCounters();
  });

  const blogUrl = post?.blog_url ?? blog?.url ?? '';
  showPostOptions(platforms);
  fillPostOptions(post?.targets.find((t) => t.platform === 'linkedin'), { blog: Boolean(blogUrl) });
  renderBlogOptions(blogUrl);
  setSource(blogUrl ? 'blog' : 'custom');
  body.value = post?.body ?? '';
  if (blog) fillFromBlog();

  renderDayPosts(day);
  renderTargetStatus(post);
  setReadOnly(Boolean(published));
  if (published) $('li-options').hidden = true; // the live post's state is shown per platform below
  updateCounters();

  if (!dialog.open) dialog.showModal();
  (published ? $('post-delete') : body).focus();
}

// ---- events --------------------------------------------------------------

body.addEventListener('input', updateCounters);
blogPick.addEventListener('change', () => { body.value = ''; fillFromBlog(); });
form.querySelectorAll('input[name=source]').forEach((r) => r.addEventListener('change', () => {
  setSource(source());
  if (source() === 'blog') {
    suggestLinkCard();
    blogPick.focus();
  }
}));
initPostOptions(updateCounters);
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

  const extra = readPostOptions(platforms, body.value);
  if (extra.error) return void (err.textContent = extra.error);
  const payload = {
    body: body.value,
    scheduled_at: fromInputs(date, time).toISOString(),
    platforms,
    blog_url: source() === 'blog' && blogPick.value ? blogPick.value : null,
    variants: extra.variants,
    options: extra.options,
  };
  const save = $('post-save');
  save.disabled = true;
  try {
    if (editing) await api.updatePost(editing.id, payload);
    else await api.createPost(payload);
    dialog.close();
    toast(editing ? 'Post updated' : `Scheduled for ${fmt.shortDate(fromInputs(date, time))} at ${time}`);
    announcePostsChanged();
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
    ? 'Remove this post from the planner? It stays live on the platforms. '
      + 'To take it down on LinkedIn, use “Delete on LinkedIn” first.'
    : 'Delete this scheduled post?';
  if (!editing || !confirm(msg)) return;
  try {
    await api.deletePost(editing.id);
    dialog.close();
    toast('Post removed');
    announcePostsChanged();
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
