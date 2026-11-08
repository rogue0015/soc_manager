// A post's deliveries in the composer: per platform its status, link, warning and
// statistics, plus "Edit on …" / "Delete on …" where the platform supports it
// (PlatformInfo.features). Both act on the live post, so both ask first.

import { api } from './api.js';
import { state } from './state.js';
import { fmt, h, textLength, toast } from './util.js';

const $ = (id) => document.getElementById(id);

// Statistic keys -> words (plural, singular), in the order they're shown.
export const STAT_LABELS = [
  ['impressions', 'impressions', 'impression'], ['members_reached', 'reached', 'reached'],
  ['engagements', 'engagements', 'engagement'], ['reactions', 'reactions', 'reaction'],
  ['comments', 'comments', 'comment'], ['reshares', 'reposts', 'repost'], ['clicks', 'clicks', 'click'],
  ['link_clicks', 'link clicks', 'link click'], ['saves', 'saves', 'save'],
];
const STATUS = { published: ['✓', 'ok', 'published'], failed: ['!', 'bad', 'failed'],
  scheduled: ['…', '', 'scheduled'], publishing: ['…', '', 'sending'] };

/** "1,234 impressions · 56 reactions" for a target's statistics, or ''. */
export function statsLine(metrics, limit = STAT_LABELS.length) {
  if (!metrics) return '';
  return STAT_LABELS.filter(([k]) => metrics[k] != null).slice(0, limit)
    .map(([k, many, one]) => `${fmt.num(metrics[k])} ${metrics[k] === 1 ? one : many}`).join(' · ');
}

function statsSource(metrics) {
  const when = metrics.updated_at ? fmt.ago(new Date(metrics.updated_at)) : '';
  return metrics.source === 'api' ? `from LinkedIn, ${when}` : `imported ${when}`;
}

function editor(post, t, p, done) {
  const text = h('textarea', { rows: 5, 'aria-label': `New text for the ${p.name} post` });
  text.value = t.text;
  const counter = h('span', { class: 'counter' });
  const update = () => {
    const n = textLength(p, text.value);
    counter.textContent = `${n}/${p.char_limit}`;
    counter.classList.toggle('over', n > p.char_limit);
  };
  text.addEventListener('input', update);
  update();
  const save = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, `Save on ${p.name}`);
  save.addEventListener('click', async () => {
    if (!confirm(`Change the live ${p.name} post to this text? It updates publicly right away.`)) return;
    save.disabled = true;
    $('composer-error').textContent = '';
    try {
      const updated = await api.editLive(post.id, t.platform, text.value);
      toast(`${p.name} post updated`);
      done(updated);
    } catch (ex) {
      $('composer-error').textContent = ex.message;
      save.disabled = false;
    }
  });
  const box = h('div', { class: 'live-editor' }, text,
    h('div', { class: 'row between' }, counter,
      h('span', { class: 'row' },
        h('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: () => box.remove() }, 'Cancel'), save)));
  setTimeout(() => text.focus());
  return box;
}

function line(post, t, done) {
  const p = state.byId[t.platform] ?? { name: t.platform, color: 'var(--muted)', features: [] };
  const [icon, tone, word] = STATUS[t.status] ?? ['', '', t.status];
  const live = t.status === 'published' && t.external_id && !t.removed_at;
  const notes = [];
  if (t.removed_at) notes.push(h('span', { class: 'muted' }, `Deleted on ${p.name} ${fmt.ago(new Date(t.removed_at))}`));
  else if (t.edited_at) notes.push(h('span', { class: 'muted' }, `Edited ${fmt.ago(new Date(t.edited_at))}`));
  if (t.warning) notes.push(h('span', { class: 'warn-text' }, t.warning));
  if (t.error) notes.push(h('span', { class: 'err' }, t.error));
  if (t.metrics) {
    notes.push(h('span', { class: 'stats-line', title: statsSource(t.metrics) },
      statsLine(t.metrics), h('span', { class: 'muted' }, ` (${statsSource(t.metrics)})`)));
  }

  const actions = [];
  const row = h('div', { class: 'target-block' });
  if (live && p.features?.includes('edit')) {
    actions.push(h('button', {
      class: 'btn btn-ghost btn-sm', type: 'button',
      onclick: () => { row.querySelector('.live-editor')?.remove(); row.append(editor(post, t, p, done)); },
    }, `Edit on ${p.name}`));
  }
  if (live && p.features?.includes('delete')) {
    actions.push(h('button', {
      class: 'btn btn-danger btn-sm', type: 'button',
      onclick: async (e) => {
        const btn = e.currentTarget; // gone from the event once we await
        if (!confirm(`Delete this post from ${p.name}? It disappears there for everyone and can't be undone. `
          + 'The planner keeps a record.')) return;
        btn.disabled = true;
        $('composer-error').textContent = '';
        try {
          const updated = await api.deleteLive(post.id, t.platform);
          toast(`Deleted from ${p.name}`);
          done(updated);
        } catch (ex) {
          $('composer-error').textContent = ex.message;
          btn.disabled = false;
        }
      },
    }, `Delete on ${p.name}`));
  }

  row.append(...[ // append() would print a null as the text "null", so drop the empty parts
    h('div', { class: 'target-line' },
      h('span', { class: 'swatch', style: { background: p.color }, 'aria-hidden': 'true' }),
      h('strong', {}, p.name),
      h('span', { class: `state ${tone}` }, icon ? h('span', { 'aria-hidden': 'true' }, `${icon} `) : null,
        t.removed_at ? 'deleted' : word),
      t.external_url && !t.removed_at ? h('a', { href: t.external_url, target: '_blank', rel: 'noopener' }, 'View post') : null),
    notes.length ? h('div', { class: 'target-notes' }, notes) : null,
    actions.length ? h('div', { class: 'row target-actions' }, actions) : null,
  ].filter(Boolean));
  return row;
}

/** Render a post's deliveries into the composer. `done(post)` runs after a live edit or delete. */
export function renderTargets(post, done) {
  const box = $('target-status');
  if (!post || post.targets.every((t) => t.status === 'scheduled')) { box.replaceChildren(); return; }
  box.replaceChildren(...post.targets.map((t) => line(post, t, done)));
}
