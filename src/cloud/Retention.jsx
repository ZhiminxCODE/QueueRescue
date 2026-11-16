import React from 'react';
import { duration, retentionLabels } from './retention.js';

const stamp = seconds => Number.isFinite(seconds) && seconds > 0 ? new Date(seconds * 1000).toLocaleString() : 'Unknown';

export function RetentionCell({ value }) {
  const labels = retentionLabels(value);
  return <div className="cloud-retention-cell"><span className={labels.originalWarning ? 'amber-text' : ''}>{labels.original}</span>
    <small className={labels.snapshotWarning ? 'amber-text' : ''}>{labels.snapshot}</small><small>{labels.age}</small></div>;
}

export function RetentionDetail({ value }) {
  const labels = retentionLabels(value), original = value?.original || {}, snapshot = value?.snapshot || {};
  return <section className="cloud-retention-detail" aria-label="Message and snapshot retention"><h3>Message and snapshot retention</h3>
    <div className="cloud-retention-cards"><div className={labels.originalWarning ? 'cloud-retention-warning' : ''}><h4>Original standard-SQS message</h4><strong>{labels.original}</strong>
      <p>{labels.age}</p><p>Estimated expiry: {stamp(original.estimatedExpiresAt)}</p>
      <p>Observed DLQ retention: {duration(value?.queue?.periodSeconds)}</p><p>Settings checked: {stamp(value?.queue?.checkedAt)}</p>
      {original.status === 'UNKNOWN' && <p>{labels.reason}</p>}
      <p>Presence is unverified. An expiry estimate does not prove that the original still exists or has been deleted.</p></div>
      <div className={labels.snapshotWarning ? 'cloud-retention-warning' : ''}><h4>Snapshot access</h4><strong>{labels.snapshot}</strong>
        <p>Access deadline: {stamp(snapshot.accessExpiresAt)}</p><p>{snapshot.availability === 'READ_SUCCEEDED' ? 'The masked payload was read for this inspection.' : 'Storage availability has not been checked.'}</p>
        <p>This deadline controls snapshot access. S3 lifecycle or other cleanup may remove stored evidence earlier.</p></div></div>
    <p>Warnings begin within 24 hours of each deadline. Estimates are as of {stamp(value?.asOf)}; refresh to update. Queue setting changes can take time to propagate.</p>
    <p>Collection does not renew SQS retention. QueueRescue does not delete originals; SQS expiry and other consumers can remove them. A snapshot can remain inspectable after the original is gone, subject to its own access deadline, permissions and storage availability.</p>
  </section>;
}
