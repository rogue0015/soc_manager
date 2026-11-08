// Connectors (Status tab): one card per platform with its setup state, a checklist
// of the keys it needs (names only), the next step, a live Check for connected
// ones, and "Sign in with …" for browser sign-ins (LinkedIn). That button is a plain
// link: the backend redirects to the platform and back here with ?signin_ok / ?signin_error.
// Also the test post form, which can go to any connected platform.

import { api } from './api.js';
import { refreshPosts, state } from './state.js';
import { announcePostsChanged, h, prefs, textLength, toast } from './util.js';

const $ = (id) => document.getElementById(id);

export const DEFAULT_TEST_MESSAGE =
  'Hello 👋 This is a test message from my own social account manager. ' +
  'If you can read this, I can now send posts here straight from my portal. More soon.';

let connectors = [];       // from /api/connectors: setup state, no network calls behind it
const statuses = {};       // live checks: platform id -> AccountStatus, or null while checking

// ---- connector cards -----------------------------------------------------------

/** The badge on each card. Text + icon carry the state; colour only repeats it. */
function badge(c, live) {
  if (c.state === 'connected') {
    if (!live) return { tone: 'muted', icon: '…', text: 'Checking' };
    if (live.can_post) return { tone: 'ok', icon: '✓', text: 'Connected' };
    if (live.connected) return { tone: 'warn', icon: '!', text: 'Can’t post yet' };
    return { tone: 'bad', icon: '!', text: 'Problem' };
  }
  if (c.state === 'needs_sign_in') return { tone: 'warn', icon: '!', text: 'Needs sign-in' };
  if (c.state === 'needs_keys') return { tone: 'warn', icon: '!', text: 'Needs keys' };
  return { tone: 'muted', icon: '', text: 'Planned' };
}

function checklist(c, name) {
  if (!c.checklist.length) return null;
  return h('ul', { class: 'checklist', 'aria-label': `${name} setup` }, c.checklist.map((item) =>
    h('li', { class: item.done ? 'done' : 'todo' },
      h('span', { class: 'mark', 'aria-hidden': 'true' }, item.done ? '✓' : '○'),
      h('span', { class: 'sr-only' }, item.done ? 'Done: ' : 'Still to do: '),
      h('span', { class: 'label' }, item.label),
      h('code', {}, item.key))));
}

function connectorCard(c) {
  const p = state.byId[c.platform] ?? { name: c.platform, color: 'var(--muted)' };
  const live = statuses[c.platform];
  const b = badge(c, live);
  const titleId = `connector-${c.platform}`;
  let message;
  if (c.state === 'connected') message = h('p', { class: 'connector-msg' }, live ? live.message : 'Checking the connection…');
  else if (c.state === 'planned') message = h('p', { class: 'connector-msg muted' }, c.next_step);
  else message = h('p', { class: 'connector-msg' }, h('strong', {}, 'Next: '), c.next_step);

  return h('article', { class: `connector ${c.state}`, 'aria-labelledby': titleId },
    h('header', { class: 'connector-head' },
      h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' }),
      h('h3', { id: titleId }, p.name),
      h('span', { class: `badge-state ${b.tone}` },
        b.icon ? h('span', { 'aria-hidden': 'true' }, b.icon) : null, b.text)),
    h('p', { class: 'connector-auth' }, c.auth),
    live?.account ? h('p', { class: 'connector-account' }, `Signed in as ${live.account}`) : null,
    checklist(c, p.name),
    message,
    c.built && c.note ? h('p', { class: 'connector-note' }, c.note) : null,
    c.redirect_uri && c.state !== 'connected'
      ? h('p', { class: 'connector-note' }, `${p.name}’s developer portal must list this redirect URL (Auth tab): `,
        h('code', { class: 'redirect' }, c.redirect_uri))
      : null,
    h('footer', { class: 'connector-foot' },
      c.sign_in_url
        ? h('a', {
          class: `btn btn-sm ${c.state === 'connected' ? 'btn-ghost' : 'btn-primary'}`,
          href: c.sign_in_url,
          onclick: () => { $('signin-error').textContent = ''; },
        }, c.state === 'connected' ? 'Sign in again' : `Sign in with ${p.name}`)
        : null,
      c.state === 'connected'
        ? h('button', {
          class: 'btn btn-ghost btn-sm', type: 'button', disabled: !live,
          'aria-label': `Check ${p.name} connection`, onclick: () => checkPlatform(c.platform),
        }, live ? 'Check' : 'Checking…')
        : null,
      c.portal_url
        ? h('a', { class: 'portal-link', href: c.portal_url, target: '_blank', rel: 'noopener' },
          'Developer portal', h('span', { 'aria-hidden': 'true' }, ' ↗'))
        : null));
}

async function checkPlatform(id) {
  statuses[id] = null;
  renderConnections();
  try {
    statuses[id] = await api.platformStatus(id);
  } catch (e) {
    statuses[id] = { connected: false, can_post: false, account: null, message: e.message };
  }
  renderConnections();
}

// ---- test post --------------------------------------------------------------

const connectedPlatforms = () => state.platforms.filter((p) => p.has_adapter);
const testPlatform = () => state.byId[$('test-post-platform').value];

function fillTestPlatforms() {
  const select = $('test-post-platform');
  const ids = connectedPlatforms().map((p) => p.id);
  if ([...select.options].map((o) => o.value).join() === ids.join()) return; // keep an open dropdown open
  const wanted = select.value || prefs.get('testPlatform', ids[0]);
  select.replaceChildren(...connectedPlatforms().map((p) => h('option', { value: p.id }, p.name)));
  select.value = ids.includes(wanted) ? wanted : (ids[0] ?? '');
}

function updateTestPost() {
  const platform = testPlatform();
  if (!platform) return;
  const text = $('test-post-text').value;
  const counter = $('test-post-counter');
  const n = textLength(platform, text);
  counter.textContent = `${n}/${platform.char_limit}`;
  counter.classList.toggle('over', n > platform.char_limit);
  $('test-post-send').textContent = `Post to ${platform.name} now`;
}

// ---- render -----------------------------------------------------------------

export function renderConnections() {
  const count = (s) => connectors.filter((c) => c.state === s).length;
  const setup = count('needs_keys') + count('needs_sign_in');
  $('connector-summary').textContent = connectors.length
    ? [`${count('connected')} of ${connectors.length} connected`, setup && `${setup} ${setup === 1 ? 'needs' : 'need'} setup`,
      count('planned') && `${count('planned')} planned`].filter(Boolean).join(' · ')
    : '';
  // Platforms that can post come first and large; the planned ones sit below in a compact row.
  const built = connectors.filter((c) => c.built);
  const planned = connectors.filter((c) => !c.built);
  $('connector-list').replaceChildren(
    h('div', { class: 'connectors' }, built.map(connectorCard)),
    planned.length ? h('h3', { class: 'group-title' }, 'Planned: not built yet') : null,
    planned.length ? h('div', { class: 'connectors compact' }, planned.map(connectorCard)) : null);
  $('test-post').hidden = !connectedPlatforms().length;
  fillTestPlatforms();
  updateTestPost();
}

/** Coming back from a platform's sign-in page: say how it went, then tidy the address bar. */
function showSignInResult() {
  const params = new URLSearchParams(location.search);
  const ok = params.get('signin_ok');
  const error = params.get('signin_error');
  if (!ok && !error) return;
  history.replaceState(null, '', location.pathname + location.hash);
  if (ok) toast(ok);
  if (error) $('signin-error').textContent = error;
}

export async function initConnections() {
  showSignInResult();
  const text = $('test-post-text');
  text.value = prefs.get('testMessage', DEFAULT_TEST_MESSAGE);
  text.addEventListener('input', () => {
    prefs.set('testMessage', text.value);
    updateTestPost();
  });
  $('test-post-platform').addEventListener('change', (e) => {
    prefs.set('testPlatform', e.target.value);
    $('test-post-result').textContent = '';
    updateTestPost();
  });
  $('test-post-form').addEventListener('submit', sendTestPost);

  try {
    connectors = await api.connectors();
  } catch (e) {
    $('connector-list').replaceChildren(h('p', { class: 'form-error', role: 'alert' }, e.message));
    return;
  }
  renderConnections();
  for (const c of connectors.filter((c) => c.state === 'connected')) checkPlatform(c.platform);
}

async function sendTestPost(e) {
  e.preventDefault();
  const result = $('test-post-result');
  const platform = testPlatform();
  const msg = $('test-post-text').value.trim();
  if (!platform || !msg) return;
  if (!confirm(`Post this publicly on ${platform.name} right now?\n\n${msg}`)) return;
  const btn = $('test-post-send');
  btn.disabled = true;
  result.className = 'result';
  result.textContent = 'Posting…';
  try {
    const r = await api.testPost(platform.id, msg);
    result.className = 'result ok';
    const manage = platform.features?.includes('edit')
      ? ' It’s on today’s calendar and in Analytics; open it there to edit or delete it.' : '';
    result.replaceChildren('Posted. ',
      r.external_url ? h('a', { href: r.external_url, target: '_blank', rel: 'noopener' }, `View on ${platform.name}`) : '',
      manage);
    toast(`Test post sent to ${platform.name}`);
    announcePostsChanged();
    refreshPosts();
  } catch (ex) {
    result.className = 'result bad';
    result.textContent = ex.message;
  } finally {
    btn.disabled = false;
  }
}
