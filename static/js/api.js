// @ts-check
// Talking to the server.

export class ApiError extends Error {
  /** @param {string} message @param {number} status @param {any} [body] */
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/**
 * fetch wrapper: JSON in/out, and every failure (network, HTTP error, non-JSON reply) becomes an ApiError.
 * @param {string} path
 * @param {RequestInit & {json?: any}} [options]  `json` is sent as the request body (and makes it a POST)
 * @returns {Promise<any>}
 */
export async function api(path, options = {}) {
  const {json, ...opts} = options;
  if (json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = {...(opts.headers || {}), "Content-Type": "application/json"};
    opts.body = JSON.stringify(json);
  }
  let r;
  try { r = await fetch(path, opts); }
  catch { throw new ApiError("Couldn't reach the server. Check your connection and try again.", 0); }
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  if (!r.ok) {
    const msg = (body && body.error) ||
      (r.status === 401 ? "Login required. Reload the page to sign in." : `The server returned an error (HTTP ${r.status}).`);
    throw new ApiError(msg, r.status, body);
  }
  if (body === null) throw new ApiError("The server sent an unexpected response.", r.status);
  return body;
}

/**
 * Run an async action with a spinner on its button, disabled meanwhile.
 * @template T
 * @param {HTMLButtonElement | null} btn
 * @param {() => Promise<T>} fn
 * @returns {Promise<T>}
 */
export async function busy(btn, fn) {
  if (btn) { btn.disabled = true; btn.classList.add("busy"); }
  try { return await fn(); }
  finally { if (btn && btn.isConnected) { btn.disabled = false; btn.classList.remove("busy"); } }
}
