// Status tab: system health tiles, problems that need attention, and recent activity.
// Refreshes every 30 s while the tab is open. It reads only the backend's own
// state; the live platform checks stay on the Connected apps card (connections.js).

import { api } from './api.js';
import { state } from './state.js';
import { currentTab, onShow } from './tabs.js';
import { fmt, h } from './util.js';

const POLL_MS = 30_000;
const $ = (id) => document.getElementById(id);

// Icons carry the meaning together with the text, never colour alone.
const ICON = {
  published: '✓', test_post: '✓', signed_in: '✓', failed: '!', interrupted: '!', scheduled: '+', blog_planned: '+',
  edited: '✎', deleted: '×', removed: '×', retried: '↻', blog_synced: '↓', started: '▸',
};
const TONE = { published: 'ok', test_post: 'ok', signed_in: 'ok', failed: 'bad', interrupted: 'bad' };

function tile(label, value, sub, tone = '') {
  return h('div', { class: `tile ${tone}` },
    h('div', { class: 'tile-label' }, label),
    h('div', { class: 'tile-value' },
      tone ? h('span', { class: 'tile-icon', 'aria-hidden': 'true' }, tone === 'ok' ? '✓' : '!') : null,
      value),
    sub ? h('div', { class: 'tile-sub' }, sub) : null);
}

function renderSystem(s) {
  const { scheduler: sch, queue: q, blog } = s;
  const connected = state.platforms.filter((p) => p.has_adapter).map((p) => p.name);

  let schedulerTile;
  if (!sch.enabled) schedulerTile = tile('Scheduler', 'Off', 'Scheduled posts won’t be sent', 'bad');
  else schedulerTile = tile('Scheduler', `Every ${sch.interval_seconds} s`,
    sch.last_run_at ? `Last check ${fmt.ago(new Date(sch.last_run_at))}` : 'Starting…',
    sch.last_error ? 'bad' : 'ok');

  const next = q.next_due_at ? `Next: ${fmt.when(new Date(q.next_due_at))}` : 'Nothing scheduled';
  $('system-tiles').replaceChildren(
    tile('Backend', 'Running', `Up ${fmt.duration(s.uptime_seconds)} · database ${fmt.bytes(s.database.size_bytes)}`, 'ok'),
    schedulerTile,
    tile('Post queue', `${fmt.num(q.scheduled)} scheduled`, q.failed ? `${next} · ${fmt.num(q.failed)} failed` : next),
    tile('Connected apps', `${s.platforms.connected} of ${s.platforms.known}`,
      connected.length ? connected.join(', ') : 'None yet', s.platforms.connected ? 'ok' : 'bad'),
    tile('Blog library', fmt.count(blog.posts, 'post'),
      blog.last_synced_at ? `Synced ${fmt.ago(new Date(blog.last_synced_at))}` : 'Not synced yet'));

  const notes = s.attention.length
    ? s.attention.map((text) => h('li', { class: 'bad' }, h('span', { class: 'icon', 'aria-hidden': 'true' }, '!'), text))
    : [h('li', { class: 'ok' }, h('span', { class: 'icon', 'aria-hidden': 'true' }, '✓'), 'All systems normal.')];
  $('system-attention').replaceChildren(...notes);
}

function activityItem(a) {
  const when = new Date(a.at);
  const platform = a.platform ? state.byId[a.platform] : null;
  return h('li', { class: 'activity-item' },
    h('span', { class: `activity-icon ${TONE[a.kind] ?? ''}`, 'aria-hidden': 'true' }, ICON[a.kind] ?? '•'),
    h('div', { class: 'activity-main' },
      h('div', { class: 'activity-msg' },
        platform ? h('span', { class: 'swatch', style: { background: platform.color }, 'aria-hidden': 'true' }) : null,
        a.message),
      h('div', { class: 'muted small' },
        h('time', { datetime: a.at, title: fmt.dateTime(when) }, fmt.ago(when)),
        a.url ? [' · ', h('a', { href: a.url, target: '_blank', rel: 'noopener' }, 'View post')] : null)));
}

function renderActivity(items) {
  $('activity-list').replaceChildren(...(items.length
    ? items.map(activityItem)
    : [h('li', { class: 'muted small' }, 'Nothing has happened yet. Scheduled and published posts will show up here.')]));
}

let busy = false;
async function refresh() {
  if (busy) return;
  busy = true;
  const btn = $('system-refresh');
  btn.disabled = true;
  try {
    const [system, activity] = await Promise.all([api.system(), api.activity(20)]);
    renderSystem(system);
    renderActivity(activity);
    $('system-updated').textContent = `Updated ${fmt.time(new Date())}`;
  } catch (e) {
    // Keep the last good numbers on screen; say what went wrong above them.
    $('system-attention').replaceChildren(
      h('li', { class: 'bad', role: 'alert' }, h('span', { class: 'icon', 'aria-hidden': 'true' }, '!'), e.message));
  } finally {
    busy = false;
    btn.disabled = false;
  }
}

export function initStatus() {
  $('system-refresh').addEventListener('click', refresh);
  onShow('status', refresh);
  setInterval(() => { if (currentTab() === 'status' && !document.hidden) refresh(); }, POLL_MS);
}
