// Composer dialog: create a post for a day, or view/edit an existing one.

import { api } from './api.js';
import { fillDevtoOptions, initDevtoOptions, readDevtoOptions, showDevtoOptions } from './devto-options.js';
import { fillMedia, initPostMedia, readMedia, showMedia } from './post-media.js';
import { openHandoff } from './handoff.js';
import { renderTargets } from './live.js';
import { confirmAction } from './modal.js';
import {
  fillPostOptions, initPostOptions, isHandoff, linkedinText, readPostOptions, setFirstComment, showPostOptions,
  suggestLinkCard,
  suggestLinkedinText,
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
let afterSave = null;    // called with the saved post (AI tab: remember which draft became a post)
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
const chosenBlogUrl = () => (source() === 'blog' && blogPick.value ? blogPick.value : null);
const isArticle = (p) => p.features?.includes('article');

function updateCounters() {
  const ids = selectedPlatforms();
  $('post-counters').replaceChildren(...ids.map((id) => {
    const p = state.byId[id];
    if (isArticle(p)) return h('span', { class: 'counter' }, `${p.name}: whole blog post`);
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

const showArticleOptions = () => showDevtoOptions(selectedPlatforms(), chosenBlogUrl());

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
  $('post-now').hidden = readOnly;
}

/** "LinkedIn", "LinkedIn and Bluesky", "X, LinkedIn and Bluesky". */
function nameList(ids) {
  const names = ids.map((id) => state.byId[id]?.name ?? id);
  return names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}

// ---- open ----------------------------------------------------------------

/**
 * openComposer({ date })          new post on a day
 * openComposer({ post })          view / edit an existing post
 * openComposer({ media })         new post with a library picture or video (Media tab)
 * openComposer({ blog, date? })   new post prefilled from a blog post
 * openComposer({ platform })      new post for one platform (Social tab)
 * openComposer({ platform, text, blog?, firstComment?, onSaved })  a ready-written draft (AI tab)
 */
export function openComposer({ date, post, blog, platform, media, text, firstComment, onSaved } = {}) {
  editing = post ?? null;
  afterSave = onSaved ?? null;
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

  const platforms = post ? post.targets.map((t) => t.platform)
    : platform ? [platform] : prefs.get('lastPlatforms', ['x']);
  renderPlatformPicks($('platform-picks'), platforms, () => {
    prefs.set('lastPlatforms', selectedPlatforms());
    showPostOptions(selectedPlatforms());
    showMedia(selectedPlatforms());
    showArticleOptions();
    fillFromBlog();
    updateCounters();
  });

  const blogUrl = post?.blog_url ?? blog?.url ?? '';
  showPostOptions(platforms);
  fillPostOptions(post?.targets.find((t) => t.platform === 'linkedin'), { blog: Boolean(blogUrl) });
  showMedia(platforms);
  fillMedia(post, media);
  fillDevtoOptions(post?.targets.find((t) => t.platform === 'devto'));
  renderBlogOptions(blogUrl);
  setSource(blogUrl ? 'blog' : 'custom');
  showArticleOptions();
  body.value = post?.body ?? '';
  if (text != null) body.value = text;   // a written draft wins over the blog's own text
  else if (blog) fillFromBlog();
  if (firstComment) setFirstComment(firstComment);

  renderDayPosts(day);
  renderTargetStatus(post);
  setReadOnly(Boolean(published));
  if (published) { // the live post's state is shown per platform below
    $('li-options').hidden = true;
    $('devto-options').hidden = true;
  }
  $('media-options').hidden = Boolean(published);
  updateCounters();

  if (!dialog.open) dialog.showModal();
  (published ? $('post-delete') : body).focus();
}

// ---- events --------------------------------------------------------------

body.addEventListener('input', updateCounters);
blogPick.addEventListener('change', () => { body.value = ''; fillFromBlog(); showArticleOptions(); });
form.querySelectorAll('input[name=source]').forEach((r) => r.addEventListener('change', () => {
  setSource(source());
  showArticleOptions();
  if (source() === 'blog') {
    suggestLinkCard();
    blogPick.focus();
  }
}));
initPostOptions(updateCounters);
initPostMedia(updateCounters);
initDevtoOptions(chosenBlogUrl);
dialog.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dialog.close()));

/** The post as the API takes it, or {error} with a sentence to show. `now`: post right away. */
function readForm({ now = false } = {}) {
  const platforms = selectedPlatforms();
  const { value: date } = $('post-date');
  const { value: time } = $('post-time');
  if (!body.value.trim()) return { error: 'Write something to post.' };
  if (!platforms.length) return { error: 'Choose at least one platform.' };
  if (!now && (!date || !time)) return { error: 'Pick a date and time.' };

  const extra = readPostOptions(platforms, body.value);
  if (extra.error) return { error: extra.error };
  const devto = readDevtoOptions(platforms, chosenBlogUrl());
  if (devto.error) return { error: devto.error };
  // Other platforms that can show a link preview (Bluesky) get one for a blog post or a link,
  // the way their own apps do. LinkedIn has its own choice above.
  const cards = Object.fromEntries(platforms
    .filter((id) => id !== 'linkedin' && state.byId[id].features.includes('link_card')
      && (chosenBlogUrl() || /https?:\/\//.test(body.value)))
    .map((id) => [id, { attachment: 'link_card' }]));
  const base = { ...cards, ...extra.options, ...devto.options };
  // A first comment on a platform this window has no field for (Bluesky, from the AI content plan)
  // is kept when the post is edited, instead of being dropped on save.
  for (const target of editing?.targets ?? []) {
    const comment = target.options?.first_comment;
    if (comment && target.platform !== 'linkedin' && platforms.includes(target.platform)) {
      // The link lives in that comment, so no automatic link preview in the post itself.
      base[target.platform] = { ...base[target.platform], first_comment: comment, attachment: 'none' };
    }
  }
  const attached = readMedia(platforms, base);
  if (attached.error) return { error: attached.error };
  return {
    payload: {
      body: body.value,
      scheduled_at: (now ? new Date() : fromInputs(date, time)).toISOString(),
      platforms,
      blog_url: chosenBlogUrl(),
      variants: extra.variants,
      options: { ...base, ...attached.options },
    },
  };
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = $('composer-error');
  err.textContent = '';
  const { payload, error } = readForm();
  if (error) return void (err.textContent = error);
  const { value: date } = $('post-date');
  const { value: time } = $('post-time');
  const save = $('post-save');
  save.disabled = true;
  try {
    const saved = editing ? await api.updatePost(editing.id, payload) : await api.createPost(payload);
    afterSave?.(saved);
    dialog.close();
    // LinkedIn's own scheduler posts it: show how to hand the text over.
    const handoff = saved.targets.find((t) => t.options?.handoff && t.status === 'scheduled');
    if (handoff) {
      toast('Saved. Now schedule it in LinkedIn.');
      openHandoff({ platform: handoff.platform, text: handoff.text, when: new Date(saved.scheduled_at) });
    } else {
      toast(editing ? 'Post updated' : `Scheduled for ${fmt.shortDate(fromInputs(date, time))} at ${time}`);
    }
    announcePostsChanged();
    await refreshAll();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    save.disabled = false;
  }
});

// Post now: save the post, then send its platforms straight away. Public and can't be unsent, so ask first.
$('post-now').addEventListener('click', async () => {
  const err = $('composer-error');
  err.textContent = '';
  const { payload, error } = readForm({ now: true });
  if (error) return void (err.textContent = error);
  if (payload.platforms.includes('linkedin') && isHandoff()) {
    return void (err.textContent = 'LinkedIn’s scheduler is chosen in the LinkedIn options. '
      + 'Pick “This app” there to post it now.');
  }
  if (!await confirmAction({
    title: `Post now on ${nameList(payload.platforms)}?`,
    message: 'It goes public right away and can’t be unsent.', confirm: 'Post now',
  })) return;

  const now = $('post-now');
  const save = $('post-save');
  now.disabled = true;
  save.disabled = true;
  now.textContent = 'Posting…';
  try {
    const saved = editing ? await api.updatePost(editing.id, payload) : await api.createPost(payload);
    editing = saved;   // if sending fails, trying again updates this post instead of making a second one
    afterSave?.(saved);
    const sent = await api.publishNow(saved.id);
    const failed = sent.targets.filter((t) => t.status === 'failed').map((t) => t.platform);
    const posted = sent.targets.filter((t) => t.status === 'published').map((t) => t.platform);
    announcePostsChanged();
    await refreshAll();
    if (!failed.length) {
      dialog.close();
      toast(`Posted on ${nameList(posted)}`);
    } else {
      openComposer({ post: sent });   // shows each platform's result and why it failed
      $('composer-error').textContent = `${nameList(failed)} didn’t go out: see why below, then press Retry failed.`
        + (posted.length ? ` Posted on ${nameList(posted)}.` : '');
    }
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    now.disabled = false;
    save.disabled = false;
    now.textContent = 'Post now';
  }
});

$('post-delete').addEventListener('click', async () => {
  const published = editing?.targets.some((t) => t.status === 'published');
  if (!editing) return;
  const sure = await confirmAction(published
    ? { title: 'Remove from the planner?', confirm: 'Remove from planner', danger: true,
      message: 'It stays live on the platforms. To take it down on LinkedIn, use “Delete on LinkedIn” first.' }
    : { title: 'Delete this scheduled post?', message: 'It won’t go out.', confirm: 'Delete', danger: true });
  if (!sure) return;
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
