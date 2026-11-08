// dev.to options in the composer: publish right away or save a draft, and a preview
// of the article dev.to will get (the whole blog post, as Markdown). Shown when dev.to
// is chosen. dev.to only takes blog posts; the backend checks that again
// (services/posts.py validate_source) and is the authority.

import { api } from './api.js';
import { state } from './state.js';
import { prefs } from './util.js';

const $ = (id) => document.getElementById(id);
const DEVTO = 'devto';
export const NEEDS_BLOG = 'dev.to only takes blog posts. Choose “From my blog” and pick a post, or untick dev.to.';

let previewUrl = null;   // the blog post the preview shows

function clearPreview() {
  previewUrl = null;
  $('devto-preview').hidden = true;
  $('devto-preview').textContent = '';
  $('devto-preview-status').textContent = '';
}

/** Show the section when dev.to is chosen; say so when there's no blog post to send. */
export function showDevtoOptions(platformIds, blogUrl) {
  $('devto-options').hidden = !platformIds.includes(DEVTO);
  $('devto-swatch').style.background = state.byId[DEVTO]?.color ?? '';
  $('devto-needs-blog').hidden = Boolean(blogUrl);
  $('devto-preview-btn').disabled = !blogUrl;
  if (blogUrl !== previewUrl) clearPreview();
}

/** Fill from an existing post's dev.to target, or with the last choice for a new post. */
export function fillDevtoOptions(target) {
  const draft = target ? Boolean(target.options?.draft) : prefs.get('devtoDraft', false);
  $('devto-publish').checked = !draft;
  clearPreview();
}

/** For the API. Returns an error sentence instead when dev.to can't take this post. */
export function readDevtoOptions(platformIds, blogUrl) {
  if (!platformIds.includes(DEVTO)) return { options: {} };
  if (!blogUrl) return { error: NEEDS_BLOG };
  const draft = !$('devto-publish').checked;
  prefs.set('devtoDraft', draft);
  return { options: { [DEVTO]: { draft } } };
}

async function preview(blogUrl) {
  if (!blogUrl) return;
  const button = $('devto-preview-btn');
  const status = $('devto-preview-status');
  button.disabled = true;
  status.classList.remove('err');
  status.textContent = 'Reading the blog post…';
  try {
    const article = await api.blogArticle(blogUrl);
    previewUrl = blogUrl;
    $('devto-preview').textContent = `# ${article.title}\n\n${article.markdown}`;
    $('devto-preview').hidden = false;
    const words = article.markdown.split(/\s+/).filter(Boolean).length;
    status.textContent = `${words.toLocaleString()} words. This is the text dev.to gets.`;
  } catch (ex) {
    status.classList.add('err');
    status.textContent = ex.message;
  } finally {
    button.disabled = false;
  }
}

/** `blogUrl()` returns the chosen blog post's URL, or null. */
export function initDevtoOptions(blogUrl) {
  $('devto-preview-btn').addEventListener('click', () => preview(blogUrl()));
}
