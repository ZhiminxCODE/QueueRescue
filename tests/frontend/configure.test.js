import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, stat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { dashboardConnectOrigins } from '../../tools/dashboard_csp.mjs';

test('build policy uses validated public origins and keeps local builds same-origin', () => {
  assert.deepEqual(dashboardConnectOrigins({}), []);
  const env = { VITE_QR_MODE: 'aws', VITE_QR_REGION: 'us-east-1', VITE_QR_API_URL: 'https://example123.execute-api.us-east-1.amazonaws.com',
    VITE_QR_COGNITO_URL: 'https://example123.auth.us-east-1.amazoncognito.com', VITE_QR_COGNITO_CLIENT_ID: 'publicclient',
    VITE_QR_REDIRECT_URI: 'http://localhost:8000/' };
  assert.deepEqual(dashboardConnectOrigins(env), [env.VITE_QR_API_URL, env.VITE_QR_COGNITO_URL]);
  assert.throws(() => dashboardConnectOrigins({ ...env, VITE_QR_API_URL: 'https://evil.example' }));
});

test('configuration CLI writes private public-only settings and refuses to overwrite them', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'qr-dashboard-config-'));
  try {
    const deployment = join(dir, 'deployment.json'), output = join(dir, 'dashboard.env');
    await writeFile(deployment, JSON.stringify({ region: 'us-east-1', api_url: 'https://example123.execute-api.us-east-1.amazonaws.com',
      cognito_login_url: 'https://example123.auth.us-east-1.amazoncognito.com', cognito_client_id: 'publicclient',
      secret_access_key: 'must-never-export-this' }));
    const script = fileURLToPath(new URL('../../tools/configure_dashboard.mjs', import.meta.url));
    const args = [script, '--deployment', deployment, '--output', output];
    const first = spawnSync(process.execPath, args, { encoding: 'utf8' });
    assert.equal(first.status, 0, first.stderr);
    const original = await readFile(output, 'utf8');
    assert.doesNotMatch(original + first.stdout + first.stderr, /must-never-export-this/);
    assert.equal((await stat(output)).mode & 0o777, 0o600);
    const second = spawnSync(process.execPath, args, { encoding: 'utf8' });
    assert.equal(second.status, 1); assert.match(second.stderr, /already exists/);
    assert.equal(await readFile(output, 'utf8'), original);
  } finally { await rm(dir, { recursive: true, force: true }); }
});
