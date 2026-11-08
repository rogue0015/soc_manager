// Entry point: load data, set up the tabs and the top bar, render on every state change.

import { initAnalytics } from './analytics.js';
import { initBlog, renderBlog } from './blog.js';
import { renderCalendar, renderLegend } from './calendar.js';
import { openComposer } from './composer.js';
import { initConnections } from './connections.js';
import { loadPlatforms, refreshPosts, setMonth, state, subscribe } from './state.js';
import { initStatus } from './status.js';
import { currentTab, initTabs } from './tabs.js';
import { toast } from './util.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 60_000; // pick up status changes made by the background publisher

async function start() {
  try {
    await loadPlatforms();
  } catch (e) {
    initTabs(); // still show the page and its error, rather than a blank screen
    toast(e.message, { error: true });
    return;
  }
  renderLegend();
  subscribe(renderCalendar);
  subscribe(renderBlog);

  $('prev-month').addEventListener('click', () => setMonth(new Date(state.month.getFullYear(), state.month.getMonth() - 1)));
  $('next-month').addEventListener('click', () => setMonth(new Date(state.month.getFullYear(), state.month.getMonth() + 1)));
  $('today-btn').addEventListener('click', () => setMonth(new Date()));
  $('new-post-btn').addEventListener('click', () => openComposer({}));

  // Keyboard: ← / → change month on the Scheduler tab, unless a dialog, field,
  // the tab list or a chart (which use the arrows themselves) has focus.
  document.addEventListener('keydown', (e) => {
    if (currentTab() !== 'scheduler' || document.querySelector('dialog[open]')) return;
    if (e.target.closest('input, textarea, select, [role="tablist"], .cc')) return;
    if (e.key === 'ArrowLeft') $('prev-month').click();
    if (e.key === 'ArrowRight') $('next-month').click();
  });

  initConnections();
  initStatus();
  initAnalytics();
  initTabs();
  await Promise.all([refreshPosts(), initBlog()]);
  setInterval(() => { if (!document.hidden) refreshPosts(); }, POLL_MS);
}

start();
