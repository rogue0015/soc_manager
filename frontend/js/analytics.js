// Analytics tab: how posts did in a period, scoped by the one row of filters.
// The backend does the counting; this module only groups deliveries by local
// day / week / month for the chart, the same way the calendar groups by local day.

import { api } from './api.js';
import { chartTable, columnChart } from './chart.js';
import { openComposer } from './composer.js';
import { STAT_LABELS, statsLine } from './live.js';
import { state } from './state.js';
import { currentTab, onShow } from './tabs.js';
import { addDays, fmt, h, prefs } from './util.js';

const $ = (id) => document.getElementById(id);
const PAGE = 25;
const FILTERS = ['f-range', 'f-platform', 'f-source', 'f-status'];
const STATUS = {
  published: { label: 'Published', icon: '✓', tone: 'ok' },
  failed: { label: 'Failed', icon: '!', tone: 'bad' },
  scheduled: { label: 'Waiting', icon: '…', tone: '' },
  publishing: { label: 'Sending', icon: '…', tone: '' },
};

let lastReport = null;
let shown = PAGE;
let requestNo = 0;

// ---- filters ---------------------------------------------------------------

function readFilters() {
  const range = $('f-range').value;
  const now = new Date();
  const days = Number(range);
  const start = range === 'all' ? null : addDays(new Date(now.getFullYear(), now.getMonth(), now.getDate()), 1 - days);
  return {
    query: {
      start: start?.toISOString(),
      end: range === 'all' ? null : now.toISOString(),
      platform: $('f-platform').value,
      source: $('f-source').value,
      status: $('f-status').value,
    },
    start,
    end: now,
  };
}

// ---- time buckets (local time) ---------------------------------------------------

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const startOfWeek = (d) => addDays(startOfDay(d), -((d.getDay() + 6) % 7)); // Monday
const startOfMonthOf = (d) => new Date(d.getFullYear(), d.getMonth(), 1);

const UNITS = {
  day: { floor: startOfDay, next: (d) => addDays(d, 1), label: fmt.dayMonth, title: fmt.shortDate },
  week: { floor: startOfWeek, next: (d) => addDays(d, 7), label: fmt.dayMonth, title: (d) => `Week of ${fmt.dayMonth(d)}` },
  month: {
    floor: startOfMonthOf,
    next: (d) => new Date(d.getFullYear(), d.getMonth() + 1, 1),
    label: (d) => d.toLocaleDateString(undefined, { month: 'short' }),
    title: (d) => d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
  },
};

function timeline(report, start, end, metric) {
  const deliveries = report.posts.flatMap((p) => p.targets).filter((t) => t.status === metric);
  const times = deliveries.map((t) => new Date(t.happened_at));
  if (!start) {
    if (!times.length) return { unit: 'day', points: [] };
    start = new Date(Math.min(...times));
    end = new Date(Math.max(end, ...times));
  }
  const days = (end - start) / 86_400_000;
  const unitName = days <= 31 ? 'day' : days <= 182 ? 'week' : 'month';
  const unit = UNITS[unitName];
  const points = [];
  const index = new Map();
  for (let d = unit.floor(start); d <= end; d = unit.next(d)) {
    index.set(d.getTime(), points.length);
    points.push({ label: unit.label(d), title: unit.title(d), value: 0 });
  }
  for (const t of times) {
    const i = index.get(unit.floor(t).getTime());
    if (i !== undefined) points[i].value += 1;
  }
  return { unit: unitName, points };
}

// ---- rendering --------------------------------------------------------------

function tile(label, value, sub, tone = '') {
  return h('div', { class: `tile ${tone}` },
    h('div', { class: 'tile-label' }, label),
    h('div', { class: 'tile-value' },
      tone ? h('span', { class: 'tile-icon', 'aria-hidden': 'true' }, tone === 'ok' ? '✓' : '!') : null, value),
    sub ? h('div', { class: 'tile-sub' }, sub) : null);
}

function renderTiles(t) {
  const finished = t.published + t.failed;
  $('kpi-tiles').replaceChildren(
    tile('Published', fmt.num(t.published), 'deliveries to a platform', t.published ? 'ok' : ''),
    tile('Failed', fmt.num(t.failed), t.failed ? 'see “Why posts failed”' : 'none', t.failed ? 'bad' : ''),
    tile('Success rate', fmt.percent(t.success_rate),
      finished ? `${fmt.num(t.published)} of ${fmt.num(finished)} went out` : 'nothing sent yet'),
    tile('Posts', fmt.num(t.posts), 'matching the filters'));
}

function renderTimeline(report, start, end) {
  const metric = $('f-status').value || 'published';
  const { unit, points } = timeline(report, start, end, metric);
  const what = STATUS[metric].label;
  $('timeline-title').textContent = `${what} per ${unit}`;
  $('timeline-subtitle').textContent = 'Each delivery to one platform counts once. Times are in your time zone.';
  if (!points.some((p) => p.value)) {
    $('timeline-chart').replaceChildren(h('p', { class: 'empty' }, `Nothing ${what.toLowerCase()} in this period.`));
    $('timeline-table').replaceChildren();
    return;
  }
  const unitWord = what.toLowerCase();
  $('timeline-chart').replaceChildren(columnChart(points, { unit: unitWord, name: `${what} per ${unit}` }));
  $('timeline-table').replaceChildren(chartTable(points, { period: unit[0].toUpperCase() + unit.slice(1), unit: what }));
}

/** Totals of imported / fetched post statistics, as tiles (up to four, most useful first). */
function renderStatTiles(stats) {
  const shown = STAT_LABELS.filter(([k]) => stats[k] != null).slice(0, 4);
  $('stat-tiles').replaceChildren(...(stats.posts
    ? shown.map(([k, label]) => tile(label[0].toUpperCase() + label.slice(1), fmt.num(stats[k]),
      `over ${fmt.count(stats.posts, 'post')} with stats`))
    : [h('p', { class: 'empty' }, 'No post stats in this period yet. Import your LinkedIn analytics file to add them.')]));
}

function splitBar(row) {
  const parts = [['published', row.published], ['failed', row.failed], ['waiting', row.scheduled + row.publishing]];
  const total = parts.reduce((n, [, v]) => n + v, 0);
  return h('div', { class: 'split', 'aria-hidden': 'true' },
    parts.filter(([, v]) => v).map(([name, v]) => h('span', { class: name, style: { flexGrow: String(v / total) } })));
}

function renderByPlatform(rows) {
  if (!rows.length) {
    $('by-platform').replaceChildren(h('p', { class: 'empty' }, 'No posts match these filters.'));
    return;
  }
  const key = h('div', { class: 'key', 'aria-hidden': 'true' },
    h('span', {}, h('i', { class: 'published' }), '✓ Published'),
    h('span', {}, h('i', { class: 'failed' }), '! Failed'),
    h('span', {}, h('i', { class: 'waiting' }), '… Waiting'));
  const table = h('table', { class: 'data-table' },
    h('thead', {}, h('tr', {},
      ['Platform', 'Published', 'Failed', 'Waiting', 'Success'].map((c) => h('th', { scope: 'col' }, c)))),
    h('tbody', {}, rows.map((r) => {
      const p = state.byId[r.platform] ?? { name: r.platform, color: 'var(--muted)' };
      return h('tr', {},
        h('th', { scope: 'row' },
          h('span', { class: 'platform-name' }, h('span', { class: 'swatch', style: { background: p.color } }), p.name),
          splitBar(r)),
        h('td', {}, fmt.num(r.published)),
        h('td', {}, fmt.num(r.failed)),
        h('td', {}, fmt.num(r.scheduled + r.publishing)),
        h('td', {}, fmt.percent(r.success_rate)));
    })));
  $('by-platform').replaceChildren(key, table);
}

function renderReasons(reasons) {
  $('failure-reasons').replaceChildren(reasons.length
    ? h('ol', { class: 'reasons' }, reasons.map((r) =>
      h('li', {}, h('span', { class: 'count' }, `${fmt.num(r.count)}×`), h('span', {}, r.error))))
    : h('p', { class: 'empty ok' }, h('span', { 'aria-hidden': 'true' }, '✓ '), 'No failures in this period.'));
}

function targetBadge(t) {
  const p = state.byId[t.platform] ?? { name: t.platform, color: 'var(--muted)' };
  const s = t.removed_at ? { label: 'Deleted', icon: '×', tone: '' } : STATUS[t.status];
  const label = `${p.name}: ${s.label.toLowerCase()}${t.warning ? ` (${t.warning})` : ''}`;
  const content = [
    h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' }),
    h('span', { class: `ico ${s.tone}`, 'aria-hidden': 'true' }, s.icon),
    p.name,
  ];
  return t.external_url && !t.removed_at
    ? h('a', { class: 'target-badge', href: t.external_url, target: '_blank', rel: 'noopener', title: `${label}. Open the post` }, content)
    : h('span', { class: 'target-badge', title: t.error ? `${label}: ${t.error}` : label }, content);
}

function renderPosts(posts) {
  const list = $('post-list');
  if (!posts.length) {
    list.replaceChildren(h('li', { class: 'empty' }, 'No posts match these filters.'));
    $('post-list-more').hidden = true;
    return;
  }
  list.replaceChildren(...posts.slice(0, shown).map((p) => {
    const when = new Date(p.targets[0].happened_at);
    const stats = p.targets.filter((t) => t.metrics).map((t) =>
      h('span', { class: 'post-stats' }, h('span', { class: 'swatch', style: { background: state.byId[t.platform]?.color },
        'aria-hidden': 'true' }), `${state.byId[t.platform]?.name ?? t.platform}: ${statsLine(t.metrics, 4)}`));
    return h('li', { class: 'post-row' },
      h('time', { class: 'when', datetime: p.targets[0].happened_at, title: fmt.dateTime(when) }, fmt.when(when)),
      h('div', { class: 'post-main' },
        h('button', {
          type: 'button', class: 'text post-open', title: `Open this post: ${p.body.split('\n')[0]}`,
          onclick: () => openPost(p.id),
        }, p.body.split('\n')[0]),
        stats),
      h('span', { class: 'targets' }, p.targets.map(targetBadge)));
  }));
  const more = $('post-list-more');
  more.hidden = posts.length <= shown;
  more.textContent = `Show more (${fmt.num(posts.length - shown)} left)`;
}

/** Open a post in the dialog, where LinkedIn posts can be edited or deleted. */
async function openPost(id) {
  try {
    openComposer({ post: await api.getPost(id) });
  } catch (e) {
    $('analytics-error').textContent = e.message;
  }
}

// ---- loading ----------------------------------------------------------------

async function load() {
  const { query, start, end } = readFilters();
  prefs.set('analyticsFilters', Object.fromEntries(FILTERS.map((id) => [id, $(id).value])));
  const mine = ++requestNo;
  const body = $('analytics-body');
  body.classList.add('loading'); // keep the previous numbers visible, dimmed, while loading
  body.setAttribute('aria-busy', 'true');
  try {
    const report = await api.analytics(query);
    if (mine !== requestNo) return; // a newer filter change already took over
    lastReport = report;
    shown = PAGE;
    $('analytics-error').textContent = '';
    renderTiles(report.totals);
    renderStatTiles(report.totals.stats);
    renderTimeline(report, start, end);
    renderByPlatform(report.by_platform);
    renderReasons(report.failure_reasons);
    renderPosts(report.posts);
  } catch (e) {
    if (mine === requestNo) $('analytics-error').textContent = e.message;
  } finally {
    if (mine === requestNo) {
      body.classList.remove('loading');
      body.removeAttribute('aria-busy');
    }
  }
}

// ---- post statistics: import, and automatic refresh when LinkedIn allows it ----------

async function loadStatsStatus() {
  try {
    const li = (await api.statsStatus()).find((s) => s.platform === 'linkedin');
    $('stats-auto').textContent = li?.automatic ? 'Automatic stats: on' : 'Automatic stats: off (needs LinkedIn approval)';
    $('stats-auto').title = li?.reason ?? '';
    $('stats-refresh').hidden = !li?.automatic;
  } catch {
    $('stats-auto').textContent = '';
  }
}

async function statsAction(button, work, busyText) {
  const result = $('stats-result');
  button.setAttribute('aria-disabled', 'true');
  button.classList.add('busy');
  result.className = 'result';
  result.textContent = busyText;
  try {
    const message = await work();
    result.className = 'result ok';
    result.textContent = message;
    toast(message.split('.')[0]);
    await load();
  } catch (ex) {
    result.className = 'result bad';
    result.textContent = ex.message;
  } finally {
    button.removeAttribute('aria-disabled');
    button.classList.remove('busy');
  }
}

export function initAnalytics() {
  const file = $('stats-file');
  file.addEventListener('change', () => {
    const chosen = file.files[0];
    file.value = '';
    if (chosen) statsAction(file.closest('label'), async () => (await api.importStats(chosen)).message, 'Reading the file…');
  });
  $('stats-refresh').addEventListener('click', (e) => statsAction(e.currentTarget, async () => {
    const r = await api.refreshStats();
    return `Updated stats for ${fmt.count(r.updated, 'post')}.${r.problems.length ? ` Problems: ${r.problems.join(' ')}` : ''}`;
  }, 'Asking LinkedIn…'));
  onShow('analytics', loadStatsStatus);
  window.addEventListener('sm:posts-changed', () => { if (currentTab() === 'analytics') load(); });

  $('f-platform').append(...state.platforms.map((p) => h('option', { value: p.id }, p.name)));
  const saved = prefs.get('analyticsFilters', {});
  for (const id of FILTERS) {
    const select = $(id);
    if (saved[id] !== undefined && [...select.options].some((o) => o.value === saved[id])) select.value = saved[id];
    select.addEventListener('change', load);
  }
  $('analytics-filters').addEventListener('submit', (e) => e.preventDefault());
  $('post-list-more').addEventListener('click', () => {
    shown += PAGE;
    if (lastReport) renderPosts(lastReport.posts);
  });
  onShow('analytics', load);
}
