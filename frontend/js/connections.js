// Connections card: live status of each connected platform and the X test post.

import { api } from './api.js';
import { state } from './state.js';
import { h, prefs, textLength, toast } from './util.js';

const $ = (id) => document.getElementById(id);

export const DEFAULT_TEST_MESSAGE =
  'Hello X 👋 This is a test message from my own social account manager. ' +
  'If you can read this, I can now send posts here straight from my portal. More soon.';

function statusRow(platform, status) {
  let dot = '', text = 'Checking…', account = '';
  if (status) {
    dot = status.can_post ? 'ok' : status.connected ? 'warn' : 'bad';
    text = status.message;
    account = status.account ?? '';
  }
  return h('div', { class: 'connection' },
    h('span', { class: 'swatch', style: { background: platform.color, marginTop: '4px' } }),
    h('div', { class: 'info' },
      h('div', { class: 'name' }, platform.name, h('span', { class: `status-dot ${dot}`, 'aria-hidden': 'true' }),
        account ? h('span', { class: 'muted small' }, account) : null),
      h('div', { class: 'muted small' }, text)),
    h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => checkPlatform(platform) }, 'Check'));
}

const statuses = {};

async function checkPlatform(platform) {
  statuses[platform.id] = null;
  renderConnections();
  try {
    statuses[platform.id] = await api.platformStatus(platform.id);
  } catch (e) {
    statuses[platform.id] = { connected: false, can_post: false, account: null, message: e.message };
  }
  renderConnections();
}

export function renderConnections() {
  const connected = state.platforms.filter((p) => p.has_adapter);
  const others = state.platforms.filter((p) => !p.has_adapter);
  const parts = connected.map((p) => statusRow(p, statuses[p.id]));
  if (!connected.length) parts.push(h('p', { class: 'muted small' }, 'No platforms connected. Add credentials to the env file.'));
  if (others.length) parts.push(h('p', { class: 'other-platforms' }, `Not connected yet: ${others.map((p) => p.name).join(', ')}`));
  $('connection-list').replaceChildren(...parts);
  $('test-post').hidden = !state.byId.x?.has_adapter;
}

export function initConnections() {
  renderConnections();
  for (const p of state.platforms.filter((p) => p.has_adapter)) checkPlatform(p);

  const text = $('test-post-text');
  const counter = $('test-post-counter');
  text.value = prefs.get('testMessage', DEFAULT_TEST_MESSAGE);
  const update = () => {
    const x = state.byId.x;
    const n = x ? textLength(x, text.value) : text.value.length;
    counter.textContent = `${n}/${x?.char_limit ?? 280}`;
    counter.classList.toggle('over', n > (x?.char_limit ?? 280));
    prefs.set('testMessage', text.value);
  };
  text.addEventListener('input', update);
  update();

  $('test-post-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const result = $('test-post-result');
    const msg = text.value.trim();
    if (!msg) return;
    if (!confirm(`Post this publicly on X right now?\n\n${msg}`)) return;
    const btn = $('test-post-send');
    btn.disabled = true;
    result.className = 'result';
    result.textContent = 'Posting…';
    try {
      const r = await api.testPost('x', msg);
      result.className = 'result ok';
      result.replaceChildren('Posted. ', h('a', { href: r.external_url, target: '_blank', rel: 'noopener' }, 'View on X'));
      toast('Test post sent to X');
    } catch (ex) {
      result.className = 'result bad';
      result.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });
}
