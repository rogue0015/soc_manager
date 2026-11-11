// Compact post details from the calendar; editing stays in the existing composer.

import { api } from './api.js';
import { openComposer } from './composer.js';
import { confirmAction } from './modal.js';
import { state } from './state.js';
import { fmt, h, announcePostsChanged, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('post-details');
let post = null;

export function openPostDetails(selected) {
  post = selected;
  const published = post.targets.some((target) => target.status === 'published' || target.status === 'publishing');
  $('post-details-title').textContent = published ? 'Post details' : 'Scheduled post';
  $('post-details-time').textContent = fmt.dateTime(new Date(post.scheduled_at));
  $('post-details-body').textContent = post.body;
  $('post-details-targets').replaceChildren(...post.targets.map((target) => {
    const platform = state.byId[target.platform] ?? { name: target.platform, color: 'var(--muted)' };
    const status = target.removed_at ? 'Removed' : target.status;
    return h('li', { class: 'post-detail-target' },
      h('span', { class: 'swatch', style: { background: platform.color } }),
      h('span', {}, platform.name),
      h('span', { class: 'post-detail-status' }, status),
    );
  }));
  $('post-details-delete').setAttribute('aria-label', published ? 'Remove from planner' : 'Delete scheduled post');
  $('post-details-delete').title = published ? 'Remove from planner' : 'Delete scheduled post';
  $('post-details-edit').setAttribute('aria-label', published ? 'View delivery details' : 'Edit post');
  $('post-details-edit').title = published ? 'View delivery details' : 'Edit post';
  dialog.showModal();
  $('post-details-edit').focus();
}

$('post-details-edit').addEventListener('click', () => {
  const selected = post;
  dialog.close();
  if (selected) openComposer({ post: selected });
});

$('post-details-delete').addEventListener('click', async () => {
  if (!post) return;
  const selected = post;
  const published = selected.targets.some((target) => target.status === 'published');
  const confirmed = await confirmAction({
    title: published ? 'Remove from the planner?' : 'Delete this scheduled post?',
    message: published
      ? 'This only removes it from the planner. Posts already live on a platform will stay there.'
      : 'This removes the post from the planner.',
    confirm: published ? 'Remove' : 'Delete',
    danger: true,
  });
  if (!confirmed) return;
  try {
    await api.deletePost(selected.id);
    dialog.close();
    announcePostsChanged();
    toast(published ? 'Post removed from the planner' : 'Scheduled post deleted');
  } catch (error) {
    toast(error.message, { error: true });
  }
});

dialog.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
