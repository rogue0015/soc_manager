// Column chart for one series (counts over time), built from plain elements.
// Thin columns in the accent colour, clean y-axis steps, the peak labelled,
// and a read-out on hover or with ←/→ when the chart has keyboard focus.
// Every value is also in the table view next to the chart.

import { fmt, h } from './util.js';

const MAX_X_LABELS = 7;

/** Round axis steps (1, 2, 5 × 10ⁿ) with at most four steps above zero. */
function axisTicks(max) {
  let step = 1;
  for (let mag = 1; ; mag *= 10) {
    const found = [1, 2, 5].map((m) => m * mag).find((s) => Math.ceil(max / s) <= 4);
    if (found) { step = found; break; }
  }
  const top = Math.max(step, Math.ceil(max / step) * step);
  const ticks = [];
  for (let t = 0; t <= top; t += step) ticks.push(t);
  return ticks;
}

/**
 * points: [{ label: short x label, title: full period name, value: number }]
 * unit:   what one column counts, for the read-out, e.g. "published"
 */
export function columnChart(points, { unit, name }) {
  const values = points.map((p) => p.value);
  const ticks = axisTicks(Math.max(1, ...values));
  const top = ticks[ticks.length - 1];
  const peak = values.indexOf(Math.max(...values));
  const every = Math.ceil(points.length / MAX_X_LABELS);
  const readout = (i) => `${points[i].title}: ${fmt.num(points[i].value)} ${unit}`;

  const tip = h('div', { class: 'cc-tip', hidden: true });
  const live = h('div', { class: 'sr-only', 'aria-live': 'polite' });
  const cols = points.map((p, i) =>
    h('div', { class: 'cc-col' },
      h('div', { class: 'cc-bar', style: { height: `${(p.value / top) * 100}%` } },
        i === peak && p.value > 0 ? h('span', { class: 'cc-peak' }, fmt.num(p.value)) : null)));

  let active = -1;
  function show(i) {
    cols[active]?.classList.remove('active');
    active = i;
    plot.classList.toggle('reading', i >= 0); // the read-out replaces the peak label while shown
    if (i < 0) { tip.hidden = true; return; }
    cols[i].classList.add('active');
    tip.textContent = readout(i);
    tip.hidden = false;
    // Centre the read-out over its column, kept inside the chart.
    const box = plot.getBoundingClientRect();
    const col = cols[i].getBoundingClientRect();
    const x = col.left - box.left + col.width / 2;
    tip.style.left = `${Math.min(Math.max(x - tip.offsetWidth / 2, 0), box.width - tip.offsetWidth)}px`;
  }

  const plot = h('div', {
    class: 'cc-plot',
    onpointermove: (e) => {
      const box = plot.getBoundingClientRect();
      const i = Math.floor(((e.clientX - box.left) / box.width) * points.length);
      if (i !== active && i >= 0 && i < points.length) show(i);
    },
    onpointerleave: () => show(-1),
  },
  ticks.map((t) => h('div', { class: 'cc-grid', style: { bottom: `${(t / top) * 100}%` } },
    h('span', { class: 'cc-tick' }, fmt.num(t)))),
  h('div', { class: 'cc-cols' }, cols),
  tip);

  const chart = h('div', {
    class: 'cc',
    role: 'group',
    tabindex: '0',
    'aria-label': `${name}. Use the left and right arrow keys to read each column.`,
    onkeydown: (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const i = Math.min(Math.max(active < 0 ? (step > 0 ? 0 : points.length - 1) : active + step, 0), points.length - 1);
      show(i);
      live.textContent = readout(i);
    },
    onblur: () => show(-1),
  },
  plot,
  h('div', { class: 'cc-x', 'aria-hidden': 'true' },
    points.map((p, i) => h('span', {}, i % every === 0 ? p.label : ''))),
  live);
  return chart;
}

/** The same numbers as a table, for screen readers and anyone who prefers figures. */
export function chartTable(points, { period, unit }) {
  return h('table', { class: 'data-table' },
    h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, period), h('th', { scope: 'col' }, unit))),
    h('tbody', {}, points.map((p) =>
      h('tr', {}, h('th', { scope: 'row' }, p.title), h('td', {}, fmt.num(p.value))))));
}
