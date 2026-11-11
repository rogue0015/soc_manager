// Validate an Excel (or CSV) preview before adding its scheduled posts to the planner.

import { api } from './api.js';
import { refreshPosts } from './state.js';
import { announcePostsChanged, h, toast } from './util.js';

const $ = (id) => document.getElementById(id);
const dialog = $('post-import-dialog');
const fileInput = $('post-import-file');
const submit = $('post-import-submit');
const status = $('post-import-status');
const errors = $('post-import-errors');
let selectedFile = null;

function reset() {
  selectedFile = null;
  fileInput.value = '';
  submit.disabled = true;
  status.textContent = '';
  status.className = 'result';
  errors.replaceChildren();
}

function showResult(result) {
  errors.replaceChildren(...result.errors.map((error) =>
    h('li', {}, `Row ${error.row}: ${error.message}`)));
  if (result.valid) {
    status.textContent = `${result.posts} ${result.posts === 1 ? 'post' : 'posts'} ready to schedule.`;
    status.classList.add('ok');
  } else {
    status.textContent = 'Fix these rows in Excel, save, and choose the file again.';
    status.classList.add('bad');
    // Browsers skip the change event when the same file is picked again, so clear the choice.
    fileInput.value = '';
  }
  submit.disabled = !result.valid;
}

export function initPostImport() {
  $('post-import-open').addEventListener('click', () => {
    reset();
    dialog.showModal();
    fileInput.focus();
  });

  dialog.querySelectorAll('[data-close]').forEach((button) =>
    button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', reset);

  fileInput.addEventListener('change', async () => {
    selectedFile = fileInput.files?.[0] ?? null;
    submit.disabled = true;
    errors.replaceChildren();
    if (!selectedFile) {
      status.textContent = '';
      return;
    }
    status.textContent = 'Checking the file…';
    status.className = 'result';
    fileInput.disabled = true;
    try {
      showResult(await api.importScheduledPosts(selectedFile, true));
    } catch (error) {
      status.textContent = error.message;
      status.className = 'result bad';
    } finally {
      fileInput.disabled = false;
    }
  });

  submit.addEventListener('click', async () => {
    if (!selectedFile) return;
    submit.disabled = true;
    fileInput.disabled = true;
    status.textContent = 'Importing…';
    status.className = 'result';
    try {
      const result = await api.importScheduledPosts(selectedFile, false);
      if (!result.valid) {
        showResult(result);
        return;
      }
      dialog.close();
      announcePostsChanged();
      await refreshPosts();
      toast(`Imported ${result.created} scheduled ${result.created === 1 ? 'post' : 'posts'}.`);
    } catch (error) {
      status.textContent = error.message;
      status.className = 'result bad';
    } finally {
      fileInput.disabled = false;
      if (dialog.open) submit.disabled = !selectedFile || errors.childElementCount > 0;
    }
  });
}
