import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { dashboardConfig } from '../../src/cloud/config.js';
import { createAuth, pkceChallenge } from '../../src/cloud/auth.js';
import { createClient } from '../../src/cloud/client.js';

const env = { VITE_QR_MODE: 'aws', VITE_QR_REGION: 'us-east-1', VITE_QR_API_URL: 'https://example123.execute-api.us-east-1.amazonaws.com',
  VITE_QR_COGNITO_URL: 'https://example123.auth.us-east-1.amazoncognito.com', VITE_QR_COGNITO_CLIENT_ID: 'example123', VITE_QR_REDIRECT_URI: 'http://localhost:5173/' };
const config = dashboardConfig(env, new URL(env.VITE_QR_REDIRECT_URI));
function storage() { const values = new Map(); return { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k), values }; }
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

test('AWS configuration fails closed for arbitrary endpoints and wrong callback origins', () => {
  assert.equal(dashboardConfig({}, new URL('http://localhost:5173/')).mode, 'local');
  for (const update of [{ VITE_QR_MODE: 'other' }, { VITE_QR_API_URL: 'https://evil.example' },
    { VITE_QR_COGNITO_URL: 'https://example123.auth.us-west-2.amazoncognito.com' }, { VITE_QR_REDIRECT_URI: 'http://evil.example/' }]) {
    assert.throws(() => dashboardConfig({ ...env, ...update }, new URL('http://localhost:5173/')));
  }
  assert.throws(() => dashboardConfig(env, new URL('http://localhost:8000/')));
});

test('PKCE matches RFC 7636', async () => {
  assert.equal(await pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk', webcrypto), 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
});

test('callback is state-bound, single-use, scrubbed before exchange, and stores only access token', async () => {
  const store = storage(); let cleaned = false; let calls = 0;
  const auth = createAuth(config, { storage: store, crypto: webcrypto, now: () => 10000, fetcher: async (url, options) => {
    assert.ok(cleaned); calls++; assert.equal(url, config.loginUrl + '/oauth2/token');
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
    assert.ok(options.body.get('code_verifier')); assert.equal(options.body.get('client_secret'), null);
    return json({ access_token: 'access-token', token_type: 'Bearer', expires_in: 900, id_token: 'private-id', refresh_token: 'private-refresh' });
  } });
  const authorize = new URL(await auth.begin());
  assert.equal(authorize.searchParams.get('code_challenge_method'), 'S256');
  const callback = config.redirectUri + '?code=code123&state=' + authorize.searchParams.get('state');
  await auth.complete(callback, url => { assert.equal(url, config.redirectUri); cleaned = true; });
  assert.equal(auth.session().accessToken, 'access-token');
  assert.doesNotMatch(JSON.stringify([...store.values]), /private-id|private-refresh|code123|verifier/);
  await assert.rejects(auth.complete(callback, () => {}), /verified/);
  assert.equal(calls, 1);
});

test('mismatched state and expired login flows never send an authorization code', async () => {
  const store = storage(); let clock = 10000, calls = 0;
  const auth = createAuth(config, { storage: store, crypto: webcrypto, now: () => clock, fetcher: async () => { calls++; } });
  await auth.begin();
  await assert.rejects(auth.complete(config.redirectUri + '?code=secret&state=wrong', () => {}), /verified/);
  const url = new URL(await auth.begin()); clock += 600001;
  await assert.rejects(auth.complete(config.redirectUri + '?code=secret&state=' + url.searchParams.get('state'), () => {}), /verified/);
  assert.equal(calls, 0);
});

test('access expiry clears the session and logout uses the configured public client', async () => {
  const store = storage(); let clock = 10000;
  const auth = createAuth(config, { storage: store, crypto: webcrypto, now: () => clock, fetcher: async () => json({ access_token: 'token', token_type: 'Bearer', expires_in: 60 }) });
  const url = new URL(await auth.begin());
  await auth.complete(config.redirectUri + '?code=c&state=' + url.searchParams.get('state'), () => {});
  assert.ok(auth.session()); clock += 56000; assert.equal(auth.session(), null);
  const logout = new URL(auth.logoutUrl()); assert.equal(logout.pathname, '/logout');
  assert.equal(logout.searchParams.get('logout_uri'), config.redirectUri);
  assert.equal(store.values.size, 0);
});

test('ambiguous mutation persists exact key/body across a reload and blocks new writes', async () => {
  const store = storage(); const actor = { id: 'operator-1' }; const auth = { session: () => ({ accessToken: 'token' }) }; const sent = [];
  const options = { storage: store, uuid: () => 'same-request-key', now: () => 10000, fetcher: async (url, request) => {
    sent.push([url, request.headers['Idempotency-Key'], request.body]);
    if (sent.length === 1) throw new TypeError('lost response');
    return json({ id: 'job-1' });
  } };
  const client = createClient(config, auth, options);
  await assert.rejects(client.write('/replay-jobs', { approvedPlanId: 'plan-1' }, actor), error => error.uncertain);
  await assert.rejects(client.write('/collections', {}, actor), /saved request/);
  const reloaded = createClient(config, auth, options);
  await assert.rejects(reloaded.retry({ id: 'approver-2' }), /original requester/);
  assert.equal((await reloaded.retry(actor)).id, 'job-1');
  assert.deepEqual(sent[0], sent[1]); assert.equal(reloaded.pending(), null);
});

test('AWS client rejects demo/arbitrary routes before transmitting authorization', async () => {
  let calls = 0;
  const client = createClient(config, { session: () => ({ accessToken: 'secret' }) }, { storage: storage(), fetcher: async () => { calls++; } });
  for (const path of ['/demo/session', '//evil.example', '/messages/../session', '/session?token=leak']) await assert.rejects(client.get(path));
  assert.equal(calls, 0);
});

test('expired sessions send no request and definitive rejections permit a corrected action', async () => {
  const store = storage(); let calls = 0; let active = false;
  const client = createClient(config, { session: () => active ? { accessToken: 'access' } : null }, {
    storage: store, uuid: () => 'request-key-1', fetcher: async () => { calls++; return json({ error: 'Rejected' }, 400); },
  });
  await assert.rejects(client.get('/session'), error => error.status === 401); assert.equal(calls, 0);
  active = true;
  await assert.rejects(client.write('/replay-plans', {}, { id: 'operator' }), /Rejected/);
  assert.equal(client.pending(), null);
});

test('a rejection after an uncertain response retains the pending request for reconciliation', async () => {
  const store = storage(); let calls = 0;
  const client = createClient(config, { session: () => ({ accessToken: 'access' }) }, {
    storage: store, uuid: () => 'request-key-1', fetcher: async () => ++calls === 1 ? json({ error: 'Unavailable' }, 503) : json({ error: 'Denied' }, 403),
  });
  await assert.rejects(client.write('/replay-jobs', { approvedPlanId: 'plan-1' }, { id: 'operator' }));
  await assert.rejects(client.retry({ id: 'operator' }), error => error.uncertain);
  assert.ok(client.pending());
});
