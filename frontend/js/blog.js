// Blog library card and the "plan selected posts" dialog.

import { api } from './api.js';
import { openComposer, pickedPlatforms, renderPlatformPicks } from './composer.js';
import { refreshAll, refreshBlog, state } from './state.js';
import { addDays, dateKey, fmt, fromInputs, h, prefs, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const selected = new Set();   // blog URLs ticked in the list

function visiblePosts() {
  const onlyNew = $('blog-unscheduled-only').checked;
  return state.blog.filter((b) => !onlyNew || !b.scheduled_count);
}

export function renderBlog() {
  // Drop selections that are no longer visible (e.g. just got scheduled).
  const visible = visiblePosts();
  const visibleUrls = new Set(visible.map((b) => b.url));
  for (const url of selected) if (!visibleUrls.has(url)) selected.delete(url);

  const total = state.blog.length;
  const unscheduled = state.blog.filter((b) => !b.scheduled_count).length;
  const synced = state.blog[0]?.fetched_at;
  $('blog-meta').textContent = total
    ? `${total} posts, ${unscheduled} not yet scheduled. Synced ${fmt.shortDate(new Date(synced))}.`
    : 'No posts yet. Press Sync to import from rogue0015.github.io/blogs.';

  $('blog-list').replaceChildren(...visible.map((b) => h('li', { class: 'blog-item' },
    h('input', {
      type: 'checkbox', checked: selected.has(b.url), 'aria-label': `Select ${b.title}`,
      onchange: (e) => { e.target.checked ? selected.add(b.url) : selected.delete(b.url); updateActions(); },
    }),
    h('div', {},
      h('button', { type: 'button', class: 'title', title: 'Schedule this post', onclick: () => openComposer({ blog: b }) }, b.title),
      h('div', { class: 'meta' }, b.published_on ?? '',
        b.scheduled_count ? h('span', { class: 'badge' }, `scheduled ×${b.scheduled_count}`) : null)),
  )));
  if (!visible.length && total) $('blog-list').append(h('li', { class: 'muted small' }, 'Every blog post is scheduled.'));
  updateActions();
}

function updateActions() {
  const n = selected.size;
  $('blog-plan-btn').disabled = n === 0;
  $('blog-plan-btn').textContent = n ? `Plan ${n} selected` : 'Plan selected';
  const all = visiblePosts();
  $('blog-select-all').textContent = all.length && all.every((b) => selected.has(b.url)) ? 'Clear' : 'Select all';
}

async function sync() {
  const btn = $('blog-sync');
  btn.disabled = true;
  btn.textContent = 'Syncing…';
  try {
    state.blog = await api.syncBlog();
    toast(`Imported ${state.blog.length} blog posts`);
  } catch (e) {
    toast(e.message, { error: true });
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sync';
    renderBlog();
  }
}

// ---- plan dialog -----------------------------------------------------------

/** The selected posts in the order they'll go out: oldest first, so the backlog reads in sequence. */
function planOrder() {
  return state.blog
    .filter((b) => selected.has(b.url))
    .sort((a, b) => (a.published_on ?? '').localeCompare(b.published_on ?? ''));
}

function renderPlanPreview() {
  const start = fromInputs($('plan-date').value, $('plan-time').value);
  const every = Number($('plan-every').value);
  const items = planOrder();
  if (Number.isNaN(start.getTime())) { $('plan-preview').replaceChildren(); return; }
  $('plan-preview').replaceChildren(...items.map((b, i) =>
    h('li', {}, `${fmt.shortDate(addDays(start, i * every))}: ${b.title}`)));
}

function openPlan() {
  const items = planOrder();
  $('plan-summary').textContent = `${items.length} post${items.length === 1 ? '' : 's'}, oldest first. You can edit or move any of them afterwards.`;
  $('plan-date').value = dateKey(addDays(new Date(), 1));
  $('plan-date').min = dateKey(new Date());
  $('plan-error').textContent = '';
  renderPlatformPicks($('plan-platforms'), prefs.get('lastPlatforms', ['x']), () => {});
  renderPlanPreview();
  $('plan-dialog').showModal();
}

async function submitPlan(e) {
  e.preventDefault();
  const platforms = pickedPlatforms($('plan-platforms'));
  if (!platforms.length) return void ($('plan-error').textContent = 'Choose at least one platform.');
  const start = fromInputs($('plan-date').value, $('plan-time').value);
  try {
    const created = await api.planBlog({
      urls: planOrder().map((b) => b.url),
      start: start.toISOString(),
      every_days: Number($('plan-every').value),
      platforms,
    });
    prefs.set('lastPlatforms', platforms);
    selected.clear();
    $('plan-dialog').close();
    toast(`Scheduled ${created.length} posts`);
    await refreshAll();
  } catch (ex) {
    $('plan-error').textContent = ex.message;
  }
}

export async function initBlog() {
  $('blog-sync').addEventListener('click', sync);
  $('blog-unscheduled-only').addEventListener('change', renderBlog);
  $('blog-select-all').addEventListener('click', () => {
    const all = visiblePosts();
    const clearing = all.every((b) => selected.has(b.url));
    for (const b of all) clearing ? selected.delete(b.url) : selected.add(b.url);
    renderBlog();
  });
  $('blog-plan-btn').addEventListener('click', openPlan);
  $('plan-form').addEventListener('submit', submitPlan);
  for (const id of ['plan-date', 'plan-time', 'plan-every']) $(id).addEventListener('input', renderPlanPreview);
  $('plan-dialog').querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => $('plan-dialog').close()));

  await refreshBlog();
  if (!state.blog.length) await sync(); // first run: import automatically
}
