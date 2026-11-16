const readPaths = /^\/(session|connections|messages(?:\/[A-Za-z0-9_-]+(?:\/evidence)?)?|incidents(?:\/[a-f0-9]{64}(?:\/messages)?)?|replay-plans(?:\/[A-Za-z0-9_-]+)?|replay-jobs(?:\/[A-Za-z0-9_-]+)?|audit)$/;
const writePaths = /^\/(collections|replay-plans(?:\/[A-Za-z0-9_-]+\/approve)?|replay-jobs(?:\/[A-Za-z0-9_-]+\/(?:pause|resume|cancel))?|verifications)$/;

export class APIError extends Error {
  constructor(message, status = 0, uncertain = false) { super(message); this.status = status; this.uncertain = uncertain; }
}

export function createClient(config, auth, { storage = sessionStorage, fetcher = fetch, uuid = () => crypto.randomUUID(), now = Date.now } = {}) {
  const key = 'qr-aws:' + config.binding + ':pending';
  let sending = false;
  function pending() {
    const raw = storage.getItem(key);
    if (!raw) return null;
    try {
      const value = JSON.parse(raw);
      if (value.binding !== config.binding || !writePaths.test(value.path) || typeof value.subject !== 'string' ||
          typeof value.requestKey !== 'string' || value.requestKey.length < 8 || typeof value.body !== 'string' ||
          !Number.isFinite(value.createdAt)) throw new Error();
      JSON.parse(value.body);
      return value;
    } catch { throw new APIError('Saved request cannot be verified. Ask your administrator to reconcile this tab before continuing.'); }
  }
  async function request(path, method, body, requestKey, params) {
    const session = auth.session();
    if (!session) throw new APIError('Session expired. Sign in again to continue.', 401);
    const suffix = params ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined)) : '';
    let response;
    try {
      response = await fetcher(config.apiUrl + '/api' + path + suffix, {
        method, credentials: 'omit', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(35000),
        headers: { Authorization: 'Bearer ' + session.accessToken,
          ...(method === 'POST' ? { 'Content-Type': 'application/json', 'Idempotency-Key': requestKey } : {}) },
        ...(method === 'POST' ? { body } : {}),
      });
    } catch { throw new APIError('Response unavailable. The request may have completed; retry the saved request.', 0, method === 'POST'); }
    let result;
    try { result = await response.json(); }
    catch { throw new APIError('The server response could not be read.', response.status, method === 'POST'); }
    if (!response.ok) throw new APIError(typeof result.error === 'string' ? result.error : 'AWS request failed.', response.status,
      method === 'POST' && (response.status >= 500 || [408, 429].includes(response.status)));
    return result;
  }
  async function get(path, params) {
    if (!readPaths.test(path)) throw new APIError('Unsupported AWS read route.');
    return request(path, 'GET', undefined, undefined, params);
  }
  async function retry(actor) {
    if (sending) throw new APIError('Wait for the current request to finish.');
    const value = pending();
    if (!value || value.subject !== actor.id) throw new APIError('Sign in as the original requester to recover this saved request.');
    if (value.createdAt > now() || now() - value.createdAt >= 6 * 86400000) throw new APIError('This saved request requires administrator reconciliation before retrying.');
    sending = true;
    try {
      const result = await request(value.path, 'POST', value.body, value.requestKey);
      storage.removeItem(key);
      return result;
    } catch (error) {
      if (error.uncertain || value.uncertain) {
        storage.setItem(key, JSON.stringify({ ...value, uncertain: true }));
        error.uncertain = true;
      } else storage.removeItem(key);
      throw error;
    } finally { sending = false; }
  }
  async function write(path, data, actor) {
    if (!writePaths.test(path)) throw new APIError('Unsupported AWS mutation route.');
    if (pending()) throw new APIError('Resolve the saved request before starting another action.');
    if (!actor?.id) throw new APIError('An authenticated server identity is required.');
    storage.setItem(key, JSON.stringify({ binding: config.binding, subject: actor.id, path,
      body: JSON.stringify(data), requestKey: uuid(), createdAt: now(), uncertain: false }));
    return retry(actor);
  }
  return { get, write, retry, pending };
}
