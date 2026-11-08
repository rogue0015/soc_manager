// Top-level tabs: Status, Social, Analytics, Scheduler.
// The open tab is kept in the URL hash (#analytics) so reload and links work,
// and remembered for the next visit. Views register onShow() to load lazily.

import { prefs } from './util.js';

const TABS = ['status', 'social', 'analytics', 'scheduler'];
const DEFAULT_TAB = 'status';
const handlers = new Map(TABS.map((t) => [t, []]));
let current = null;

const tabButton = (name) => document.getElementById(`tab-${name}`);
const panel = (name) => document.getElementById(`panel-${name}`);

export const currentTab = () => current;

/** Run `fn` every time `tab` is shown (and right away if it already is). */
export function onShow(tab, fn) {
  handlers.get(tab).push(fn);
  if (current === tab) fn();
}

export function showTab(name, { focus = false } = {}) {
  if (!TABS.includes(name)) name = DEFAULT_TAB;
  for (const t of TABS) {
    const selected = t === name;
    tabButton(t).setAttribute('aria-selected', String(selected));
    tabButton(t).tabIndex = selected ? 0 : -1; // roving tabindex: Tab enters the list once
    panel(t).hidden = !selected;
  }
  if (focus) tabButton(name).focus();
  if (current === name) return;
  current = name;
  prefs.set('tab', name);
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  handlers.get(name).forEach((fn) => fn());
}

export function initTabs() {
  const list = document.querySelector('[role="tablist"]');
  list.addEventListener('click', (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (tab) showTab(tab.dataset.tab);
  });
  // Arrow keys move between tabs (WAI-ARIA tabs pattern, automatic activation).
  list.addEventListener('keydown', (e) => {
    const i = TABS.indexOf(current);
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    showTab(TABS[(next + TABS.length) % TABS.length], { focus: true });
  });
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));

  const fromUrl = location.hash.slice(1);
  showTab(TABS.includes(fromUrl) ? fromUrl : prefs.get('tab', DEFAULT_TAB));
}
