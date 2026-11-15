import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, stat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

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
