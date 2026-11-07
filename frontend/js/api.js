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

async function request(method, path, data) {
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

export const api = {
  platforms: () => request('GET', '/api/platforms'),
  platformStatus: (id) => request('GET', `/api/platforms/${id}/status`),
  testPost: (id, text) => request('POST', `/api/platforms/${id}/test`, { text }),

  posts: (start, end) =>
    request('GET', `/api/posts?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  createPost: (post) => request('POST', '/api/posts', post),
  updatePost: (id, post) => request('PUT', `/api/posts/${id}`, post),
  deletePost: (id) => request('DELETE', `/api/posts/${id}`),
  retryPost: (id) => request('POST', `/api/posts/${id}/retry`),

  blogPosts: () => request('GET', '/api/blog/posts'),
  syncBlog: () => request('POST', '/api/blog/sync'),
  planBlog: (plan) => request('POST', '/api/blog/plan', plan),
};
