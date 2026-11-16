import test from 'node:test';
import assert from 'node:assert/strict';
import { reportMarkdown, costPayload } from '../../src/cloud/report.js';
import { createClient } from '../../src/cloud/client.js';

const fixture = () => ({ reportVersion: 1, generatedAt: 1800000000, scope: 'INDEXED_INCIDENT_SAMPLE', region: 'us-east-1',
  incident: { id: 'a'.repeat(64) }, counts: { recovered: 1, skipped: 1, blocked: 0, unresolved: 1 },
  coverage: { completeForIndexedHistory: false, issues: ['PLAN_LIMIT_REACHED'] },
  verification: { attemptedOperations: 2, checkedOperations: 1, completeOperations: 1, completePercent: 50 },
  duration: { startedAt: 1799999900, resolvedAt: null, resolvedDurationSeconds: null, elapsedSeconds: 100 },
  hypotheses: [{ code: 'CAUSE_NOT_ESTABLISHED', confidence: 'INCONCLUSIVE', text: 'No confirmed root cause.', evidenceIds: [] }],
  cost: { measurement: null, estimate: null, notice: 'Missing data is not zero cost. Estimates are not measurements.' },
  evidence: [], rows: [{ id: 'b'.repeat(64), disposition: 'UNRESOLVED', reasonCode: 'NO_COMPLETION_EVIDENCE' }],
  limitations: ['No live business verification.'], reportHash: 'c'.repeat(64) });

test('report Markdown preserves partial coverage, unknown costs and verification denominators', () => {
  const value = fixture(); value.privatePayload = 'never-export-private-payload';
  const markdown = reportMarkdown(value);
  assert.match(markdown, /\| 1 \| 1 \| 0 \| 1 \|/);
  assert.match(markdown, /Indexed history complete: no/);
  assert.match(markdown, /PLAN_LIMIT_REACHED/);
  assert.match(markdown, /Distinct attempted operations: 2; checked: 1; recorded COMPLETE: 1/);
  assert.match(markdown, /Measurement: unavailable/);
  assert.match(markdown, /not full incident resolution/);
  assert.doesNotMatch(markdown, /USD 0|never-export-private-payload/);
  value.verification.completePercent = null;
  assert.match(reportMarkdown(value), /Verification coverage: Not applicable/);
});

test('cost observations remain separately labeled and only valid regional log links are exported', () => {
  const value = fixture();
  const common = { sourceKind: 'AWS_BILLING_EXPORT', sourceSha256: 'f'.repeat(64), periodStart: 1799999900, periodEnd: 1800000000, observedAt: 1800000000 };
  value.cost.measurement = { ...common, amountUSD: '0.00' };
  value.cost.estimate = { ...common, sourceKind: 'OFFLINE_CALCULATOR', amountUSD: '0.20' };
  value.evidence = [{ id: 'log:one', kind: 'CORRELATED_TELEMETRY', logUrl: 'javascript:private-token' },
    { id: 'log:two', kind: 'CORRELATED_TELEMETRY', logUrl: 'https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/group/log-events/stream' }];
  const markdown = reportMarkdown(value);
  assert.match(markdown, /Measurement: USD 0.00; admin recorded, provider verification: no/);
  assert.match(markdown, /Estimate: USD 0.20/);
  assert.match(markdown, /Open returned log stream/);
  assert.doesNotMatch(markdown, /javascript:|private-token/);
});

test('cost form reads submitted times and rejects unproven decimal values', () => {
  const form = new FormData();
  for (const [key, value] of Object.entries({ kind: 'MEASURED', amountUSD: '0.0123', sourceKind: 'AWS_BILLING_EXPORT',
    sourceSha256: 'f'.repeat(64), periodStart: '2026-11-16T10:00:00', periodEnd: '2026-11-16T11:00:00', observedAt: '2026-11-16T12:00:00' })) form.set(key, value);
  const result = costPayload(form);
  assert.equal(result.amountUSD, '0.0123');
  assert.equal(result.periodEnd - result.periodStart, 3600);
  form.set('amountUSD', 'NaN'); assert.throws(() => costPayload(form), /decimal/);
  form.set('amountUSD', '0'); form.set('sourceSha256', 'private'); assert.throws(() => costPayload(form), /SHA-256/);
  form.set('sourceSha256', 'f'.repeat(64)); form.set('periodEnd', '2026-11-16T09:00:00');
  assert.throws(() => costPayload(form), /ordered/);
});

test('report reads and cost writes keep strict routes and recover the same request after a lost response', async () => {
  const store = new Map(); const sent = []; let calls = 0;
  const client = createClient({ apiUrl: 'https://example123.execute-api.us-east-1.amazonaws.com', binding: 'report-fixture' },
    { session: () => ({ accessToken: 'access' }) }, { storage: { getItem: k => store.get(k) ?? null, setItem: (k,v) => store.set(k,v), removeItem: k => store.delete(k) },
      uuid: () => 'report-cost-key', fetcher: async (url, options) => { sent.push([url, options]);
        if (options.method === 'POST' && calls++ === 0) throw new TypeError('lost response');
        return new Response(JSON.stringify({ id: 'cost-fixture' }), { status: 200 }); },
  });
  const base = '/incidents/' + 'a'.repeat(64);
  await client.get(base + '/report', { evidenceMessageId: 'b'.repeat(64) });
  for (const path of [base + '/report/extra', '/incidents/../report', base + '/cost-observations']) await assert.rejects(client.get(path));
  await assert.rejects(client.write(base + '/cost-observations', { kind: 'ESTIMATED' }, { id: 'admin-1' }));
  await assert.rejects(client.retry({ id: 'viewer-1' }), /original requester/);
  await client.retry({ id: 'admin-1' });
  assert.equal(sent.length, 3);
  assert.equal(sent[0][1].method, 'GET');
  assert.equal(sent[1][1].body, sent[2][1].body);
  assert.equal(sent[1][1].headers['Idempotency-Key'], sent[2][1].headers['Idempotency-Key']);
  assert.equal(client.pending(), null);
});
