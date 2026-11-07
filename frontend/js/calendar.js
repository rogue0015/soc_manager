// Monthly calendar. One coloured chip per post per platform, so colour = platform.

import { openComposer } from './composer.js';
import { state, visibleDays } from './state.js';
import { dateKey, fmt, h, sameDay } from './util.js';

const MAX_CHIPS = 4;
const STATUS_ICON = { published: '✓', failed: '!', publishing: '…' };

function postsByDay() {
  const map = new Map();
  for (const post of state.posts) {
    const key = dateKey(new Date(post.scheduled_at));
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(post);
  }
  return map;
}

function chip(post, target) {
  const p = state.byId[target.platform] ?? { name: target.platform, color: '#888', text_color: '#fff' };
  const when = fmt.time(new Date(post.scheduled_at));
  const firstLine = post.body.split('\n')[0];
  const label = `${p.name}, ${when}, ${target.status}: ${firstLine}`;
  return h('button', {
    type: 'button',
    class: `chip ${target.status}`,
    style: { background: p.color, color: p.text_color },
    title: target.error ? `${label}\nError: ${target.error}` : label,
    'aria-label': label,
    onclick: (e) => { e.stopPropagation(); openComposer({ post }); },
  },
  STATUS_ICON[target.status] ? h('span', { class: 'status-icon', 'aria-hidden': 'true' }, STATUS_ICON[target.status]) : null,
  h('span', { class: 'chip-text' }, `${when} ${firstLine}`));
}

export function renderLegend() {
  const ul = document.getElementById('legend');
  ul.replaceChildren(...state.platforms.map((p) =>
    h('li', {}, h('span', { class: 'swatch', style: { background: p.color } }), p.name)));
}

export function renderCalendar() {
  document.getElementById('month-label').textContent = fmt.month(state.month);
  const grid = document.getElementById('calendar-grid');
  const byDay = postsByDay();
  const today = new Date();
  const todayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate());

  const cells = visibleDays().map((day) => {
    const posts = byDay.get(dateKey(day)) ?? [];
    const chips = posts.flatMap((post) => post.targets.map((t) => chip(post, t)));
    const classes = ['day'];
    if (day.getMonth() !== state.month.getMonth()) classes.push('other-month');
    if (sameDay(day, today)) classes.push('today');
    else if (day < todayStart) classes.push('past');

    const count = chips.length;
    return h('div', {
      class: classes.join(' '),
      role: 'button',
      tabindex: '0',
      'aria-label': `${fmt.day(day)}${count ? `, ${count} scheduled` : ''}`,
      onclick: () => openComposer({ date: day }),
      onkeydown: (e) => {
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          openComposer({ date: day });
        }
      },
    },
    h('span', { class: 'day-num' }, String(day.getDate())),
    chips.slice(0, MAX_CHIPS),
    count > MAX_CHIPS ? h('span', { class: 'more' }, `+${count - MAX_CHIPS} more`) : null,
    // Compact view (small screens) shows one dot per post per platform instead of chips.
    h('span', { class: 'dots', 'aria-hidden': 'true' },
      posts.flatMap((post) => post.targets.map((t) =>
        h('span', { class: 'dot', style: { background: state.byId[t.platform]?.color ?? '#888' } })))));
  });
  grid.replaceChildren(...cells);
}
