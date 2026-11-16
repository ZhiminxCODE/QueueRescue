import test from 'node:test';
import assert from 'node:assert/strict';
import { duration, retentionLabels } from '../../src/cloud/retention.js';

test('original-message estimates and snapshot access warnings remain independent', () => {
  const labels = retentionLabels({ original: { status: 'NEAR_EXPIRY', remainingSeconds: 120, enqueueAgeSeconds: 345480 },
    snapshot: { status: 'WITHIN_ACCESS_WINDOW', remainingSeconds: 604800 } });
  assert.equal(labels.original, 'Original estimate: 2m left');
  assert.equal(labels.snapshot, 'Snapshot access: 7d 0h left');
  assert.equal(labels.age, 'Enqueue age: 3d 23h');
  assert.equal(labels.originalWarning, true);
  assert.equal(labels.snapshotWarning, false);
});

test('passed expiry estimates do not claim deletion or current presence', () => {
  const labels = retentionLabels({ original: { status: 'EXPIRY_ESTIMATE_PASSED', remainingSeconds: 0 },
    snapshot: { status: 'NEAR_EXPIRY', remainingSeconds: 3600 } });
  assert.equal(labels.original, 'Original expiry estimate passed');
  assert.doesNotMatch(labels.original, /deleted|retained|present/i);
  assert.equal(labels.snapshot, 'Snapshot access: 1h 0m left');
  assert.equal(labels.snapshotWarning, true);
});

test('unknown or denied retention does not derive a deadline from snapshot retention', () => {
  for (const value of [undefined, {}, { original: { status: 'FUTURE_ENUM' } }, { queue: { status: 'DENIED' }, original: { status: 'UNKNOWN', enqueueAgeSeconds: 60 },
    snapshot: { status: 'WITHIN_ACCESS_WINDOW', remainingSeconds: 604800 } }]) {
    const labels = retentionLabels(value);
    assert.equal(labels.original, 'Original expiry unknown');
    assert.equal(labels.originalWarning, true);
  }
  assert.match(retentionLabels({ queue: { status: 'DENIED' } }).reason, /denied/);
  assert.match(retentionLabels({ original: { reason: 'TIMESTAMP_UNKNOWN' } }).reason, /enqueue time/);
  assert.equal(retentionLabels({ snapshot: { status: 'EXPIRED' } }).snapshot, 'Snapshot access expired');
});

test('unknown durations are not coerced to zero and sub-minute windows stay visible', () => {
  for (const value of [undefined, null, NaN, -1, '120']) assert.equal(duration(value), 'unknown');
  assert.equal(duration(0), 'under 1m');
  assert.equal(duration(59), 'under 1m');
  assert.equal(duration(86400), '1d 0h');
});
