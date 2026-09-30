// One client for every page and the overlay. The port and token come from Electron's main
// process (a free port and a fresh token each launch), never from the page itself.

let configPromise = null;

function config() {
  if (!configPromise) configPromise = window.lfa.config();
  return configPromise;
}

async function base() {
  const { port } = await config();
  return `http://127.0.0.1:${port}`;
}

export class ApiError extends Error {
  constructor(status, detail) {
    const text = typeof detail === 'string' ? detail : detail?.message || `Request failed (${status})`;
    super(text);
    this.status = status;
    this.detail = detail;
  }
}

export async function request(path, { method = 'GET', body, query } = {}) {
  const { token } = await config();
  const url = new URL((await base()) + path);
  for (const [k, v] of Object.entries(query || {})) if (v != null && v !== '') url.searchParams.set(k, v);
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the local backend. It may still be starting.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data.detail);
  return data;
}

/** Resolves once the backend answers /health (it takes a few seconds to start). */
export async function waitForBackend({ timeoutMs = 60000, onWaiting } = {}) {
  const started = Date.now();
  for (;;) {
    try {
      const r = await fetch((await base()) + '/health');
      if (r.ok) return true;
    } catch { /* not up yet */ }
    if (Date.now() - started > timeoutMs) return false;
    onWaiting?.();
    await new Promise((res) => setTimeout(res, 400));
  }
}

/** POST /chat and dispatch its server-sent events: results, token, done, error. */
export async function chat({ question, root }, handlers, signal) {
  const { token } = await config();
  let response;
  try {
    response = await fetch((await base()) + '/chat', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, root: root || null }),
      signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') return;
    handlers.error?.({ detail: "Can't reach the local backend. It may still be starting." });
    return;
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    handlers.error?.({ detail: data.detail || `Request failed (${response.status})` });
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1];
        const data = /^data: (.*)$/m.exec(block)?.[1];
        if (event && data) handlers[event]?.(JSON.parse(data));
      }
    }
  } catch (err) {
    if (err.name !== 'AbortError') handlers.error?.({ detail: 'The answer stream was interrupted.' });
  }
}

export const api = {
  search: (q, root) => request('/search', { query: { q, root } }),
  settings: () => request('/settings'),
  setModel: (model) => request('/settings/model', { method: 'PUT', body: { model } }),
  roots: () => request('/files/roots'),
  addRoot: (folder) => request('/files/roots', { method: 'POST', body: { folder } }),
  removeRoot: (folder) => request('/files/roots/remove', { method: 'POST', body: { folder } }),
  rescan: (folder) => request('/files/index', { method: 'POST', body: { folder } }),
  indexStatus: () => request('/files/index/status'),
  cancelIndex: () => request('/files/index/cancel', { method: 'POST' }),
  open: (path) => request('/files/open', { method: 'POST', body: { path } }),
  plan: (root) => request('/organize/plan', { method: 'POST', body: { root } }),
  apply: (actions) => request('/organize/apply', { method: 'POST', body: { actions } }),
  undo: (batch) => request('/organize/undo', { method: 'POST', body: { batch: batch || null } }),
  history: () => request('/organize/history'),
};
