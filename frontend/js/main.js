// Entry point: load data, wire the top bar, render on every state change.

import { initBlog, renderBlog } from './blog.js';
import { renderCalendar, renderLegend } from './calendar.js';
import { openComposer } from './composer.js';
import { initConnections } from './connections.js';
import { loadPlatforms, refreshPosts, setMonth, state, subscribe } from './state.js';
import { toast } from './util.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 60_000; // pick up status changes made by the background publisher

async function start() {
  try {
    await loadPlatforms();
  } catch (e) {
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

  // Keyboard: ← / → change month when no dialog or field is focused.
  document.addEventListener('keydown', (e) => {
    if (document.querySelector('dialog[open]') || e.target.closest('input, textarea, select')) return;
    if (e.key === 'ArrowLeft') $('prev-month').click();
    if (e.key === 'ArrowRight') $('next-month').click();
  });

  initConnections();
  await Promise.all([refreshPosts(), initBlog()]);
  setInterval(() => { if (!document.hidden) refreshPosts(); }, POLL_MS);
}

start();
