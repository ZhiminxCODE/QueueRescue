import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdir, mkdtemp, rm, writeFile, symlink } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { runInNewContext } from 'node:vm';
import { hostedEnvironment, hostingCsp, releaseManifest } from '../../tools/prepare_dashboard_release.mjs';

const deployment = { account_id: '123456789012', region: 'us-east-1', api_url: 'https://example123.execute-api.us-east-1.amazonaws.com', cognito_login_url: 'https://example123.auth.us-east-1.amazoncognito.com', cognito_client_id: 'publicclient', dashboard_hosting: { provisioned: true, delivery_enabled: false, domain_name: 'd123example.cloudfront.net', callback_url: 'https://d123example.cloudfront.net/callback', bucket: 'queuerescue-dev-123456789012-us-east-1-dashboard', distribution_id: 'EHOSTED123' } };

test('release configuration accepts only canonical hosted outputs and public browser fields', () => {
  const env = hostedEnvironment({ ...deployment, secret_access_key: 'never-export' });
  assert.equal(Object.keys(env).length, 6);
  assert.equal(env.VITE_QR_MODE, 'aws'); assert.equal(env.VITE_QR_REDIRECT_URI, deployment.dashboard_hosting.callback_url);
  assert.doesNotMatch(JSON.stringify(env), /never-export/);
  for (const hosting of [{ provisioned: false }, { callback_url: 'http://evil.example/' }, { callback_url: 'https://d123example.cloudfront.net/' }, { domain_name: 'd123example.cloudfront.net.evil.example' }, { bucket: 'unrelated-account' }]) {
    assert.throws(() => hostedEnvironment({ ...deployment, dashboard_hosting: { ...deployment.dashboard_hosting, ...hosting } }));
  }
  assert.throws(() => hostedEnvironment({ ...deployment, api_url: 'https://evil.example' }));
});

test('edge router rewrites only callback and canonicalizes entry paths without exposing query credentials', async () => {
  const code = await readFile(new URL('../../infra/aws/functions/dashboard-routing.js', import.meta.url), 'utf8');
  const handler = runInNewContext(code + '\nhandler;');
  const querystring = { code: { value: 'never-in-location' }, state: { value: 'secret-state' } };
  const request = { uri: '/callback', method: 'GET', querystring, headers: {}, cookies: {} };
  const result = handler({ request });
  assert.equal(result.uri, '/index.html'); assert.equal(result.querystring, querystring);
  for (const uri of ['/', '/index.html', '/callback/']) {
    const result = handler({ request: { ...request, uri } });
    assert.equal(result.statusCode, 302); assert.equal(result.headers.location.value, '/callback');
    assert.doesNotMatch(JSON.stringify(result), /never-in-location|secret-state/);
  }
  for (const uri of ['/assets/index-12345678.js', '/missing.js', '/other']) {
    assert.equal(handler({ request: { ...request, uri } }).uri, uri);
  }
});

test('release manifest rejects data, source maps and an absent or incorrect CSP', () => {
  const env = hostedEnvironment(deployment), policy = hostingCsp(env);
  const files = { 'index.html': Buffer.from('<head><meta http-equiv="Content-Security-Policy" content="' + policy + '"></head>'), 'assets/index-12345678.js': Buffer.from('public source'), 'connect-origins.json': Buffer.from(JSON.stringify([env.VITE_QR_API_URL, env.VITE_QR_COGNITO_URL])) };
  const manifest = releaseManifest(files, deployment);
  assert.equal(manifest.files.length, 3);
  assert.equal(manifest.files.find(f => f.key === 'index.html').cacheControl, 'no-store');
  assert.match(manifest.files.find(f => f.key.endsWith('.js')).cacheControl, /immutable/);
  for (const key of ['.env', 'assets/index-12345678.js.map', 'browser-benchmark.html', '../outside.js']) {
    assert.throws(() => releaseManifest({ ...files, [key]: Buffer.from('private') }, deployment));
  }
  assert.throws(() => releaseManifest({ ...files, 'index.html': Buffer.from('<head></head>') }, deployment));
  assert.throws(() => releaseManifest({ ...files, 'connect-origins.json': Buffer.from('["https://evil.example"]') }, deployment));
});

test('offline release builds into ignored storage without env leakage or overwriting existing output', async () => {
  const root = resolve('.data'); await mkdir(root, { recursive: true });
  const dir = await mkdtemp(join(root, 'hosting-test-'));
  try {
    const input = join(dir, 'deployment.json'), output = join(dir, 'release');
    await writeFile(input, JSON.stringify(deployment));
    const args = ['tools/prepare_dashboard_release.mjs', '--deployment', input, '--output', output];
    const first = spawnSync(process.execPath, args, { encoding: 'utf8', env: { ...process.env, VITE_UNEXPECTED_SECRET: 'must-not-ship-env-canary' } });
    assert.equal(first.status, 0, first.stderr);
    const manifest = JSON.parse(await readFile(join(output, 'manifest.json'), 'utf8'));
    assert.ok(manifest.files.some(f => f.key.endsWith('.css')));
    for (const file of manifest.files) assert.doesNotMatch(await readFile(join(output, 'site', file.key), 'utf8'), /must-not-ship-env-canary/);
    const second = spawnSync(process.execPath, args, { encoding: 'utf8' });
    assert.equal(second.status, 1); assert.match(second.stderr, /already exists/);
    const outside = spawnSync(process.execPath, ['tools/prepare_dashboard_release.mjs', '--deployment', input, '--output', 'dist'], { encoding: 'utf8' });
    assert.equal(outside.status, 1); assert.match(outside.stderr, /\.data/);
    await symlink(dir, join(dir, 'linked-parent'));
    const linked = spawnSync(process.execPath, ['tools/prepare_dashboard_release.mjs', '--deployment', input, '--output', join(dir, 'linked-parent', 'escaped-release')], { encoding: 'utf8' });
    assert.equal(linked.status, 1); assert.match(linked.stderr, /symbolic links/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
