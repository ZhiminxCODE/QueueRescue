import test from 'node:test';
import assert from 'node:assert/strict';
import { cloudWatchLink, evidenceWindow, localTimeInput } from '../../src/cloud/evidence.js';
import { createClient } from '../../src/cloud/client.js';

test('evidence links are restricted to the configured regional CloudWatch log viewer', () => {
  const link = 'https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/$252Faws$252Flambda$252Fdemo/log-events/stream';
  assert.equal(cloudWatchLink(link, 'us-east-1'), link);
  for (const bad of ['javascript:alert(1)', 'https://evil.example/log-events/', link.replace('us-east-1.console', 'us-west-2.console'),
    link.replace('https://', 'https://secret@'), link.replace('/cloudwatch/home', '/other'), link.replace('region=us-east-1', 'region=us-west-2'),
    link.replace('#logsV2:', '#other:'), link.replace('/log-events/stream', '')]) assert.equal(cloudWatchLink(bad, 'us-east-1'), null);
});

test('lookup windows use local input times and enforce ordering and a 24-hour maximum', () => {
  const start = localTimeInput(1800000000);
  const end = localTimeInput(1800003600);
  assert.deepEqual(evidenceWindow(start, end), { from: '1800000000', to: '1800003600' });
  assert.deepEqual(evidenceWindow('', ''), {});
  assert.throws(() => evidenceWindow(end, start), /ordered/);
  assert.throws(() => evidenceWindow(start, localTimeInput(1800086401)), /24 hours/);
  assert.throws(() => evidenceWindow('invalid', ''), /valid/);
});

test('source and exact-attempt lookups send only authenticated reads with bounded-window parameters', async () => {
  const sent = []; const config = { apiUrl: 'https://example123.execute-api.us-east-1.amazonaws.com', binding: 'fixture' };
  const client = createClient(config, { session: () => ({ accessToken: 'test-token' }) }, {
    storage: { getItem: () => null }, fetcher: async (url, options) => { sent.push([new URL(url), options]); return new Response(JSON.stringify({ items: [] })); },
  });
  const path = '/messages/' + 'a'.repeat(64) + '/evidence';
  await client.get(path);
  await client.get(path, { jobId: 'job-' + 'b'.repeat(24), attemptId: 'attempt-' + 'c'.repeat(24), from: '1800000000', to: '1800000300' });
  assert.equal(sent[0][0].pathname, '/api' + path);
  assert.equal(sent[1][0].searchParams.get('attemptId'), 'attempt-' + 'c'.repeat(24));
  assert.equal(sent[1][0].searchParams.get('from'), '1800000000');
  assert.ok(sent.every(([, request]) => request.method === 'GET' && !request.body && request.credentials === 'omit'));
  assert.equal(client.pending(), null);
  for (const bad of [path + '/extra', '/messages/../evidence', '/messages/id/evidence?filter=evil']) await assert.rejects(client.get(bad));
  assert.equal(sent.length, 2);
});
