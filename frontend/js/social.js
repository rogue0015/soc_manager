// Social tab: one account at a time, picked in the sidebar, which shows who you're signed in
// as on each platform. For the chosen account: its profile, what's coming up (including posts
// waiting in LinkedIn's own scheduler), past posts with their numbers, the best posts,
// importing or refreshing stats, and downloadable reports. Loads when the tab is shown and
// when posts change; it doesn't poll. Profiles come in after the rest, because they ask the
// platforms. See ADR 0008 and 0010.

import { api } from './api.js';
import { tile } from './analytics.js';
import { openComposer } from './composer.js';
import { openHandoff } from './handoff.js';
import { STAT_LABELS, statsLine } from './live.js';
import { state } from './state.js';
import { currentTab, onShow } from './tabs.js';
import { addDays, fmt, h, prefs, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const PAGE = 15;
const STATE_WORDS = { connected: 'Connected', needs_keys: 'Needs keys', needs_sign_in: 'Needs sign-in', planned: 'Planned' };
const STATE_TONES = { connected: 'ok', needs_keys: 'warn', needs_sign_in: 'warn', planned: 'muted' };

let selected = null;
let connectors = [];
let statsStatus = [];
let profiles = {};        // platform id -> {profile, error} for connected platforms
let profilesLoading = false;
let last = null;          // the latest overview from the API
let shownHistory = PAGE;
let requestNo = 0;

const platform = () => state.byId[selected];

function periodRange() {
  const days = $('social-period').value;
  return days === 'all' ? {} : { start: addDays(new Date(), -Number(days)).toISOString() };
}

// ---- sidebar ----------------------------------------------------------------------

const swatch = (p) => h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' });

/** The account's picture, ringed in the platform's colour; the colour swatch if there's no picture. */
function accountMark(p, prof, cls) {
  if (!prof?.avatar) return cls === 'nav-avatar' ? swatch(p) : h('span', { class: `${cls} placeholder`, style: { background: p.color }, 'aria-hidden': 'true' });
  return h('img', {
    class: cls, src: prof.avatar, alt: '', referrerpolicy: 'no-referrer', loading: 'lazy',
    style: { '--ring': p.color },
    onerror: (e) => e.target.replaceWith(swatch(p)),   // e.g. an expired picture link
  });
}

function renderNav() {
  $('social-nav-list').replaceChildren(...state.platforms.map((p) => {
    const c = connectors.find((x) => x.platform === p.id);
    const prof = profiles[p.id]?.profile;
    return h('li', {}, h('button', {
      type: 'button', class: 'social-nav-item', 'aria-current': p.id === selected ? 'true' : null,
      title: prof ? `${p.name}: ${prof.handle ?? prof.name}` : p.name,
      onclick: () => select(p.id),
    },
    accountMark(p, prof, 'nav-avatar'),
    h('span', { class: 'nav-text' },
      h('span', { class: 'name' }, p.name),
      prof ? h('span', { class: 'nav-account' }, prof.handle ?? prof.name) : null),
    c ? h('span', { class: `nav-state ${STATE_TONES[c.state] ?? ''}` }, STATE_WORDS[c.state] ?? c.state) : null));
  }));
}

function select(id) {
  selected = id;
  prefs.set('socialPlatform', id);
  shownHistory = PAGE;
  renderNav();
  load();
}

// ---- the signed-in account ---------------------------------------------------------------

function count(n, word) {
  return h('span', {}, h('strong', {}, fmt.num(n)), ` ${word}`);
}

function renderProfile() {
  const box = $('social-profile');
  const p = platform();
  const c = connectors.find((x) => x.platform === selected);
  const entry = profiles[selected];
  const toStatus = h('a', { href: '#status' }, 'Status tab');
  if (!entry || !entry.profile) {
    let message;
    if (c?.state === 'planned') message = [`${p.name} isn’t built yet. You can still plan posts for it.`];
    else if (c?.state !== 'connected') message = [`You’re not signed in to ${p.name} yet. The `, toStatus, ' says how to connect it.'];
    else if (!entry) message = [profilesLoading ? `Loading your ${p.name} profile…` : ''];
    else message = [entry.error, ' See the ', toStatus, '.'];
    box.replaceChildren(h('p', { class: 'muted small' }, ...message));
    return;
  }
  const prof = entry.profile;
  const counts = [
    prof.followers != null ? count(prof.followers, prof.followers === 1 ? 'follower' : 'followers') : null,
    prof.following != null ? count(prof.following, 'following') : null,
    prof.posts != null ? count(prof.posts, p.features.includes('article')
      ? (prof.posts === 1 ? 'article' : 'articles') : (prof.posts === 1 ? 'post' : 'posts')) : null,
  ].filter(Boolean);
  box.replaceChildren(
    accountMark(p, prof, 'profile-avatar'),
    h('div', { class: 'profile-main' },
      h('div', { class: 'profile-name' }, prof.name, prof.handle && prof.handle !== prof.name
        ? h('span', { class: 'muted' }, ` ${prof.handle}`) : null),
      prof.bio ? h('p', { class: 'profile-bio' }, prof.bio) : null,
      counts.length ? h('p', { class: 'profile-counts' }, counts)
        : h('p', { class: 'muted small' }, `${p.name} doesn’t share follower numbers with the app.`)),
    h('div', { class: 'profile-actions' },
      prof.url ? h('a', { class: 'btn btn-ghost btn-sm', href: prof.url, target: '_blank', rel: 'noopener' },
        'View profile', h('span', { 'aria-hidden': 'true' }, ' ↗')) : null,
      h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: (e) => loadProfiles(true, e.currentTarget) },
        'Refresh')));
}

async function loadProfiles(refresh = false, button = null) {
  profilesLoading = true;
  if (button) button.disabled = true;
  renderProfile();
  try {
    profiles = Object.fromEntries((await api.profiles(refresh)).map((e) => [e.platform, e]));
    if (refresh) toast('Profiles updated');
  } catch (e) {
    $('social-error').textContent = e.message;
  } finally {
    profilesLoading = false;
    if (button) button.disabled = false;
  }
  renderNav();
  renderProfile();
}

// ---- one post in a list ---------------------------------------------------------

function stateChip(item) {
  const p = platform();
  if (item.removed_at) return h('span', { class: 'state' }, 'deleted');
  if (item.handoff && item.status === 'scheduled') {
    return h('span', { class: 'state' }, h('span', { 'aria-hidden': 'true' }, '⏲ '), `in ${p.name}’s schedule`);
  }
  if (item.imported) return h('span', { class: 'state ok' }, h('span', { 'aria-hidden': 'true' }, '✓ '), 'imported');
  const [icon, tone, word] = {
    published: ['✓', 'ok', 'published'], failed: ['!', 'bad', 'failed'],
    scheduled: ['…', '', 'scheduled'], publishing: ['…', '', 'sending'],
  }[item.status] ?? ['', '', item.status];
  return h('span', { class: `state ${tone}` }, icon ? h('span', { 'aria-hidden': 'true' }, `${icon} `) : null, word);
}

async function openPost(id) {
  try {
    openComposer({ post: await api.getPost(id) });
  } catch (e) {
    $('social-error').textContent = e.message;
  }
}

function postItem(item, when) {
  const p = platform();
  const actions = [h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => openPost(item.post_id) }, 'Open')];
  if (item.external_url && !item.removed_at) {
    actions.push(h('a', { class: 'btn btn-ghost btn-sm', href: item.external_url, target: '_blank', rel: 'noopener' },
      `View on ${p.name}`));
  }
  if (item.handoff && item.status === 'scheduled' && p.compose_url) {
    actions.push(h('button', {
      type: 'button', class: 'btn btn-ghost btn-sm',
      onclick: () => openHandoff({ platform: selected, text: item.text, when: new Date(when) }),
    }, `Copy and open ${p.name}`));
  }
  return h('li', { class: 'social-item' },
    h('div', { class: 'social-item-head' },
      h('time', { datetime: when, title: fmt.dateTime(new Date(when)) }, fmt.when(new Date(when))), stateChip(item)),
    h('p', { class: 'social-text' }, item.text),
    item.metrics ? h('p', { class: 'stats-line small' }, statsLine(item.metrics)) : null,
    item.error ? h('p', { class: 'err small' }, item.error) : null,
    h('div', { class: 'row wrap social-actions' }, actions));
}

// ---- sections ------------------------------------------------------------------------

function renderHead() {
  const p = platform();
  const c = connectors.find((x) => x.platform === selected);
  $('social-name').textContent = p.name;
  $('social-swatch').style.background = p.color;
  $('social-state').textContent = c ? STATE_WORDS[c.state] ?? c.state : '';
  $('social-state').className = `badge-state ${STATE_TONES[c?.state] ?? ''}`;
  $('social-new').textContent = p.features.includes('article') ? `+ New ${p.name} article` : `+ New ${p.name} post`;
}

function renderUpcoming() {
  const items = last.upcoming;
  $('social-upcoming-count').textContent = items.length ? fmt.count(items.length, 'post') : '';
  $('social-upcoming').replaceChildren(...(items.length
    ? items.map((u) => postItem(u, u.scheduled_at))
    : [h('li', { class: 'empty' }, `Nothing planned for ${platform().name}. Press “${$('social-new').textContent}” to add one.`)]));
}

function renderStats() {
  const t = last.totals;
  const s = t.stats;
  const shown = STAT_LABELS.filter(([k]) => s[k] != null).slice(0, 3);
  $('social-tiles').replaceChildren(
    tile('Published', fmt.num(t.published), t.imported ? `${fmt.num(t.imported)} imported` : 'in this period'),
    ...shown.map(([k, label]) => tile(label[0].toUpperCase() + label.slice(1), fmt.num(s[k]),
      `over ${fmt.count(s.posts, 'post')} with stats`)));
  $('social-top').replaceChildren(...(last.top.length
    ? last.top.map((d) => postItem(d, d.happened_at))
    : [h('li', { class: 'empty' }, 'No post stats in this period yet. Import or refresh them below.')]));
}

function renderHistory() {
  const items = last.history;
  $('social-history-count').textContent = items.length ? fmt.count(items.length, 'post') : '';
  $('social-history').replaceChildren(...(items.length
    ? items.slice(0, shownHistory).map((d) => postItem(d, d.happened_at))
    : [h('li', { class: 'empty' }, 'No posts went out in this period.')]));
  const more = $('social-history-more');
  more.hidden = items.length <= shownHistory;
  more.textContent = `Show more (${fmt.num(items.length - shownHistory)} left)`;
}

function renderImport() {
  const p = platform();
  const box = $('social-import');
  const parts = [];
  const auto = statsStatus.find((s) => s.platform === selected);
  if (p.features.includes('stats_import')) {
    const input = h('input', { type: 'file', accept: '.xlsx,.csv', onchange: (e) => importFile(e) });
    parts.push(
      h('p', { class: 'muted small' }, `${p.name} doesn’t let apps read your past posts or their numbers, so bring them in from its export: on `,
        h('a', { href: 'https://www.linkedin.com/analytics/creator/content/', target: '_blank', rel: 'noopener' }, `${p.name} analytics`),
        ', choose the longest period, press Export, then choose that file here. Posts you made before using this app are added with their numbers.'),
      h('div', { class: 'row wrap' }, h('label', { class: 'btn btn-ghost btn-sm file-btn' }, `Import ${p.name} file…`, input)));
  }
  if (auto?.automatic) {
    parts.push(
      h('p', { class: 'muted small' }, `${p.name} shares views, reactions and comments with the app.`),
      h('div', { class: 'row wrap' }, h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: refresh }, 'Refresh stats')));
  }
  if (!parts.length) {
    const c = connectors.find((x) => x.platform === selected);
    parts.push(h('p', { class: 'muted small' }, c?.state === 'planned'
      ? `${p.name} isn’t connected yet. You can still plan posts for it; the Status tab says what connecting it needs.`
      : `${p.name} doesn’t share post stats with the app${auto && !auto.connected ? ' until it’s connected' : ' yet'}.`));
  }
  box.replaceChildren(...parts);
  $('social-import-result').textContent = '';
}

function renderReports() {
  const p = platform();
  const range = periodRange();
  $('social-report-note').textContent = `Every ${p.name} post in the chosen period, with its numbers. `
    + 'Excel adds a summary sheet with totals and the best posts.';
  for (const format of ['xlsx', 'csv']) {
    $(`social-report-${format}`).href = api.reportUrl({ ...range, platform: selected, format });
  }
}

// ---- actions ---------------------------------------------------------------------------

async function statsAction(button, work, busyText) {
  const result = $('social-import-result');
  button.setAttribute('aria-disabled', 'true');
  button.classList.add('busy');
  result.className = 'result';
  result.textContent = busyText;
  try {
    const message = await work();
    result.className = 'result ok';
    result.textContent = message;
    toast(message.split('.')[0]);
    await loadData();
  } catch (ex) {
    result.className = 'result bad';
    result.textContent = ex.message;
  } finally {
    button.removeAttribute('aria-disabled');
    button.classList.remove('busy');
  }
}

function importFile(e) {
  const chosen = e.target.files[0];
  e.target.value = '';
  if (chosen) statsAction(e.target.closest('label'), async () => (await api.importStats(chosen)).message, 'Reading the file…');
}

function refresh(e) {
  statsAction(e.currentTarget, async () => {
    const r = await api.refreshStats();
    return `Updated stats for ${fmt.count(r.updated, 'post')}.${r.problems.length ? ` Problems: ${r.problems.join(' ')}` : ''}`;
  }, 'Fetching stats…');
}

// ---- loading ---------------------------------------------------------------------------

async function loadData() {
  const mine = ++requestNo;
  const body = $('social-body');
  body.setAttribute('aria-busy', 'true');
  try {
    const overview = await api.social(selected, periodRange());
    if (mine !== requestNo) return;   // a newer choice already took over
    last = overview;
    $('social-error').textContent = '';
    renderUpcoming();
    renderStats();
    renderHistory();
  } catch (e) {
    if (mine === requestNo) $('social-error').textContent = e.message;
  } finally {
    if (mine === requestNo) body.removeAttribute('aria-busy');
  }
}

async function load() {
  renderHead();
  renderProfile();
  renderImport();
  renderReports();
  await loadData();
}

async function onShowSocial() {
  try {
    [connectors, statsStatus] = await Promise.all([api.connectors(), api.statsStatus()]);
  } catch (e) {
    $('social-error').textContent = e.message;
  }
  renderNav();
  loadProfiles();   // asks the platforms, so it fills in after the rest
  await load();
}

export function initSocial() {
  const saved = prefs.get('socialPlatform', 'linkedin');
  selected = state.byId[saved] ? saved : state.platforms[0]?.id;
  const period = prefs.get('socialPeriod', '90');
  if ([...$('social-period').options].some((o) => o.value === period)) $('social-period').value = period;
  $('social-period').addEventListener('change', () => {
    prefs.set('socialPeriod', $('social-period').value);
    shownHistory = PAGE;
    renderReports();
    loadData();
  });
  $('social-new').addEventListener('click', () => openComposer({ platform: selected }));
  $('social-history-more').addEventListener('click', () => {
    shownHistory += PAGE;
    renderHistory();
  });
  window.addEventListener('sm:posts-changed', () => { if (currentTab() === 'social') loadData(); });
  onShow('social', onShowSocial);
}
