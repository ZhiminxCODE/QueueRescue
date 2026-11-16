export function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'unknown';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return 'under 1m';
  if (minutes < 60) return minutes + 'm';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ' + minutes % 60 + 'm';
  return Math.floor(hours / 24) + 'd ' + hours % 24 + 'h';
}

export function retentionLabels(retention) {
  const original = retention?.original || {}, snapshot = retention?.snapshot || {};
  const originalWarning = original.status !== 'ESTIMATED_WINDOW_OPEN';
  const sourceReason = original.reason === 'TIMESTAMP_UNKNOWN' ? 'Original enqueue time is missing or invalid.' : {
    DENIED: 'DLQ settings access was denied.', MISSING_QUEUE: 'The configured DLQ was not found.',
    UNAVAILABLE: 'DLQ settings could not be read.', INVALID_METADATA: 'DLQ retention metadata could not be validated.',
    UNSUPPORTED_QUEUE: 'Queue semantics are unsupported.',
  }[retention?.queue?.status] || 'The DLQ retention period is unknown.';
  return {
    age: 'Enqueue age: ' + duration(original.enqueueAgeSeconds),
    original: original.status === 'EXPIRY_ESTIMATE_PASSED' ? 'Original expiry estimate passed' :
      ['NEAR_EXPIRY', 'ESTIMATED_WINDOW_OPEN'].includes(original.status) && Number.isFinite(original.remainingSeconds) ?
        'Original estimate: ' + duration(original.remainingSeconds) + ' left' : 'Original expiry unknown',
    originalWarning,
    snapshot: snapshot.status === 'EXPIRED' ? 'Snapshot access expired' :
      ['NEAR_EXPIRY', 'WITHIN_ACCESS_WINDOW'].includes(snapshot.status) && Number.isFinite(snapshot.remainingSeconds) ?
        'Snapshot access: ' + duration(snapshot.remainingSeconds) + ' left' : 'Snapshot access deadline unknown',
    snapshotWarning: snapshot.status !== 'WITHIN_ACCESS_WINDOW',
    reason: sourceReason,
  };
}
