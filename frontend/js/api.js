// Thin wrapper over fetch for the backend API.
// Errors are thrown as ApiError with the server's plain-language `detail`.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function detailFrom(body, status) {
  if (!body) return `Request failed (HTTP ${status})`;
  if (typeof body.detail === 'string') return body.detail;
  // FastAPI validation errors: [{loc, msg}, ...]
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join('; ');
  return `Request failed (HTTP ${status})`;
}

export async function request(method, path, data) {
  const opts = { method, headers: {} };
  if (data !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(data);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch {
    throw new ApiError('Can’t reach the server. Is it running?', 0);
  }
  if (resp.status === 204) return null;
  const body = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(detailFrom(body, resp.status), resp.status);
  return body;
}

async function upload(path, file) {
  let resp;
  try {
    resp = await fetch(path, {
      method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' },
    });
  } catch {
    throw new ApiError('Can’t reach the server. Is it running?', 0);
  }
  const body = await resp.json().catch(() => null);
  if (resp.status === 413) throw new ApiError('That file is too large.', 413);
  if (!resp.ok) throw new ApiError(detailFrom(body, resp.status), resp.status);
  return body;
}

// fetch() can't report upload progress, so library uploads use XMLHttpRequest.
function uploadWithProgress(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (e) => { if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError('Can’t reach the server. Is it running?', 0));
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 413) reject(new ApiError('That file is too large (videos up to 1 GB).', 413));
      else if (xhr.status >= 400) reject(new ApiError(detailFrom(body, xhr.status), xhr.status));
      else resolve(body);
    };
    xhr.send(file);
  });
}

export const api = {
  platforms: () => request('GET', '/api/platforms'),
  platformStatus: (id) => request('GET', `/api/platforms/${id}/status`),
  testPost: (id, text) => request('POST', `/api/platforms/${id}/test`, { text }),

  getPost: (id) => request('GET', `/api/posts/${id}`),
  posts: (start, end) =>
    request('GET', `/api/posts?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  createPost: (post) => request('POST', '/api/posts', post),
  updatePost: (id, post) => request('PUT', `/api/posts/${id}`, post),
  deletePost: (id) => request('DELETE', `/api/posts/${id}`),
  retryPost: (id) => request('POST', `/api/posts/${id}/retry`),
  /** Send the post's waiting platforms right now. Public, can't be unsent: ask first. */
  publishNow: (id) => request('POST', `/api/posts/${id}/publish`),
  editLive: (id, platform, text) => request('PUT', `/api/posts/${id}/published/${platform}`, { text }),
  deleteLive: (id, platform) => request('DELETE', `/api/posts/${id}/published/${platform}`),

  /** Images and analytics files go up as the raw request body. */
  /** Add a picture or video to the library. onProgress(0..1) reports the upload (videos can be big). */
  uploadMedia: (file, name = file.name ?? '', onProgress = null) =>
    uploadWithProgress(`/api/media?name=${encodeURIComponent(name)}`, file, onProgress),
  listMedia: (kind = '') => request('GET', `/api/media${kind ? `?kind=${kind}` : ''}`),
  deleteMedia: (id) => request('DELETE', `/api/media/${id}`),
  statsStatus: () => request('GET', '/api/stats'),
  refreshStats: () => request('POST', '/api/stats/refresh'),
  importStats: (file) => upload('/api/stats/import', file),

  /** Who you're signed in as on each connected platform (the backend keeps them 10 minutes). */
  profiles: (refresh = false) => request('GET', `/api/profiles${refresh ? '?refresh=true' : ''}`),
  /** One account for the Social tab. range: {start?, end?} as ISO strings. */
  social: (platform, range = {}) => request('GET', `/api/social/${platform}?${new URLSearchParams(range)}`),
  /** Link to a downloadable report: {format: 'csv'|'xlsx', start, end, platform, source, status}; empty values left out.
   * Times in the report are in this browser's time zone. */
  reportUrl: (params) => `/api/reports/posts?${new URLSearchParams(Object.entries(
    { ...params, utc_offset: -new Date().getTimezoneOffset() }).filter(([, v]) => v !== '' && v != null))}`,

  connectors: () => request('GET', '/api/connectors'),
  system: () => request('GET', '/api/system'),
  activity: (limit = 20) => request('GET', `/api/activity?limit=${limit}`),
  /** filters: {start, end, platform, source, status}; empty values are left out. */
  analytics: (filters) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    return request('GET', `/api/analytics?${query}`);
  },

  blogPosts: () => request('GET', '/api/blog/posts'),
  syncBlog: () => request('POST', '/api/blog/sync'),
  /** A blog post as the article dev.to gets: {title, description, tags, canonical_url, markdown}. */
  blogArticle: (url) => request('GET', `/api/blog/article?url=${encodeURIComponent(url)}`),
  planBlog: (plan) => request('POST', '/api/blog/plan', plan),
};
