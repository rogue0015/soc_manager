// Backend calls for the AI generation tab (backend/app/routes/ai.py).
// Errors come back as ApiError with the server's plain-language `detail`, as in api.js.

import { request } from './api.js';

export const aiApi = {
  /** {ready, model, per_minute, per_day, used_today, left_today, message}. No call to Gemini. */
  status: () => request('GET', '/api/ai/status'),

  personas: () => request('GET', '/api/ai/personas'),
  createPersona: (persona) => request('POST', '/api/ai/personas', persona),
  updatePersona: (id, persona) => request('PUT', `/api/ai/personas/${id}`, persona),
  deletePersona: (id) => request('DELETE', `/api/ai/personas/${id}`),
  addStarters: () => request('POST', '/api/ai/personas/starters'),

  /** {source, idea, blog_url, event: {name, day, venue, takeaways}, platforms, persona_id} → a generation. */
  generate: (req) => request('POST', '/api/ai/generate', req),
  history: (limit = 30) => request('GET', `/api/ai/generations?limit=${limit}`),
  deleteGeneration: (id) => request('DELETE', `/api/ai/generations/${id}`),
  regenerate: (id, platform) => request('POST', `/api/ai/generations/${id}/regenerate`, { platform }),
  /** Trash a draft you won't use, or bring it back with trashed = false. */
  trash: (id, platform, trashed = true) =>
    request('PUT', `/api/ai/generations/${id}/drafts/${platform}/trash`, { trashed }),
  markUsed: (id, platform, postId) =>
    request('PUT', `/api/ai/generations/${id}/drafts/${platform}/post`, { post_id: postId }),
};
