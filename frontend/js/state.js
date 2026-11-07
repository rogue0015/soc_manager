// App state shared by the views, plus the loaders that fill it.
// Views call subscribe(render); anything that changes data calls refresh*().

import { api } from './api.js';
import { addDays, startOfMonth, toast } from './util.js';

export const state = {
  platforms: [],       // from /api/platforms, in display order
  byId: {},            // platform id -> platform
  posts: [],           // posts in the visible calendar range
  blog: [],            // cached blog library
  month: startOfMonth(new Date()),
};

const listeners = new Set();
export const subscribe = (fn) => listeners.add(fn);
const notify = () => listeners.forEach((fn) => fn());

/** The days shown for a month: whole weeks, Monday first. */
export function visibleDays(month = state.month) {
  const first = startOfMonth(month);
  const start = addDays(first, -((first.getDay() + 6) % 7)); // back to Monday
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
  const end = addDays(last, 6 - ((last.getDay() + 6) % 7));  // forward to Sunday
  const days = [];
  for (let d = start; d <= end; d = addDays(d, 1)) days.push(d);
  return days;
}

export async function loadPlatforms() {
  state.platforms = await api.platforms();
  state.byId = Object.fromEntries(state.platforms.map((p) => [p.id, p]));
}

export async function refreshPosts() {
  const days = visibleDays();
  const start = days[0];
  const end = addDays(days[days.length - 1], 1);
  try {
    state.posts = await api.posts(start.toISOString(), end.toISOString());
  } catch (e) {
    toast(e.message, { error: true });
  }
  notify();
}

export async function refreshBlog() {
  try {
    state.blog = await api.blogPosts();
  } catch (e) {
    toast(e.message, { error: true });
  }
  notify();
}

export const refreshAll = () => Promise.all([refreshPosts(), refreshBlog()]);

export function setMonth(month) {
  state.month = startOfMonth(month);
  return refreshPosts();
}
