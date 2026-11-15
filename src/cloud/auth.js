const encoder = new TextEncoder();
const b64url = bytes => btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');

export async function pkceChallenge(verifier, crypto = globalThis.crypto) {
  return b64url(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(verifier))));
}

export function createAuth(config, { storage = sessionStorage, crypto = globalThis.crypto, fetcher = fetch, now = Date.now } = {}) {
  const prefix = 'qr-aws:' + config.binding;
  const sessionKey = prefix + ':session';
  const flowKey = prefix + ':pkce';
  const read = key => { try { return JSON.parse(storage.getItem(key) || 'null'); } catch { return null; } };
  const clear = () => { storage.removeItem(sessionKey); storage.removeItem(flowKey); };
  function session() {
    const value = read(sessionKey);
    if (!value || value.binding !== config.binding || typeof value.accessToken !== 'string' || !Number.isFinite(value.expiresAt) || value.expiresAt <= now() + 5000) {
      storage.removeItem(sessionKey);
      return null;
    }
    return value;
  }
  async function begin() {
    clear();
    const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
    const state = b64url(crypto.getRandomValues(new Uint8Array(32)));
    storage.setItem(flowKey, JSON.stringify({ verifier, state, binding: config.binding, createdAt: now() }));
    const url = new URL(config.loginUrl + '/oauth2/authorize');
    url.search = new URLSearchParams({ response_type: 'code', client_id: config.clientId,
      redirect_uri: config.redirectUri, scope: 'openid email queuerescue/access', state,
      code_challenge_method: 'S256', code_challenge: await pkceChallenge(verifier, crypto) });
    return url.href;
  }
  async function complete(href, cleanUrl) {
    const url = new URL(href);
    if (!url.searchParams.has('code') && !url.searchParams.has('error')) return session();
    // Remove credentials from browser history before any network request.
    cleanUrl(config.redirectUri);
    const flow = read(flowKey);
    clear();
    if (url.origin + url.pathname !== config.redirectUri ||
        !flow || flow.binding !== config.binding || !Number.isFinite(flow.createdAt) ||
        typeof flow.verifier !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(flow.verifier) ||
        now() - flow.createdAt > 600000 || flow.createdAt > now() ||
        url.searchParams.getAll('state').length !== 1 || url.searchParams.get('state') !== flow.state) throw new Error('Sign-in could not be verified. Start a new sign-in.');
    if (url.searchParams.has('error')) throw new Error('Sign-in was cancelled or refused. Start again when ready.');
    if (url.searchParams.getAll('code').length !== 1 || !url.searchParams.get('code')) throw new Error('Invalid sign-in response.');
    const response = await fetcher(config.loginUrl + '/oauth2/token', {
      method: 'POST', credentials: 'omit', redirect: 'error', cache: 'no-store',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId,
        redirect_uri: config.redirectUri, code: url.searchParams.get('code'), code_verifier: flow.verifier }),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error('Sign-in exchange failed. Start a new sign-in.');
    const result = await response.json();
    if (typeof result.access_token !== 'string' || !/^[A-Za-z0-9._~-]+$/.test(result.access_token) ||
        result.token_type?.toLowerCase() !== 'bearer' || !Number.isFinite(result.expires_in) || result.expires_in <= 5 || result.expires_in > 86400) throw new Error('Invalid access-token response.');
    // ID and refresh tokens are deliberately discarded. Server /session supplies roles.
    const value = { binding: config.binding, accessToken: result.access_token, expiresAt: now() + result.expires_in * 1000 };
    storage.setItem(sessionKey, JSON.stringify(value));
    return value;
  }
  function logoutUrl() {
    clear();
    const url = new URL(config.loginUrl + '/logout');
    url.search = new URLSearchParams({ client_id: config.clientId, logout_uri: config.redirectUri });
    return url.href;
  }
  return { session, begin, complete, clear, logoutUrl };
}
