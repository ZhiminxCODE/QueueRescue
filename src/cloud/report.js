import { cloudWatchLink } from './evidence.js';

const stamp = value => Number.isFinite(value) ? new Date(value * 1000).toISOString() : 'Not established';
const text = value => String(value ?? 'Unknown').replace(/[\r\n]/g, ' ').replace(/[<>]/g, '').replaceAll('|', '\\|');

export function reportMarkdown(report) {
  const lines = ['# QueueRescue incident report', '', `Incident: ${text(report.incident.id)}`, `Category: ${text(report.incident.label)}; consumer version: ${text(report.incident.consumerVersion)}.`,
    `Collection window: ${stamp(report.incident.windowStart)} to ${stamp(report.incident.windowEnd)}.`, text(report.summary), `Generated: ${stamp(report.generatedAt)}`,
    `Scope: ${text(report.scope)}. This is an indexed sample, not the complete DLQ.`, '', '## Outcomes', '',
    '| Recovered | Skipped | Blocked | Unresolved |', '| --- | --- | --- | --- |',
    `| ${report.counts.recovered} | ${report.counts.skipped} | ${report.counts.blocked} | ${report.counts.unresolved} |`, '',
    `Indexed history complete: ${report.coverage.completeForIndexedHistory ? 'yes' : 'no'}.`,
    `Coverage issues: ${report.coverage.issues.map(text).join(', ') || 'None detected within the read bounds'}.`, '', '## Verification and duration', '',
    `Distinct attempted operations: ${report.verification.attemptedOperations}; checked: ${report.verification.checkedOperations}; recorded COMPLETE: ${report.verification.completeOperations}.`,
    `Verification coverage: ${report.verification.completePercent === null ? 'Not applicable' : report.verification.completePercent + '%'}. No live verifier was invoked.`,
    `Recovery started: ${stamp(report.duration.startedAt)}. Recorded resolution: ${stamp(report.duration.resolvedAt)}.`,
    `Recorded resolution duration: ${report.duration.resolvedDurationSeconds ?? 'Not established'} seconds; elapsed: ${report.duration.elapsedSeconds ?? 'Not established'} seconds. This covers included replay jobs (including paused/shared-job work), not full incident resolution.`, '', '## Evidence-backed findings', ''];
  for (const finding of report.hypotheses) lines.push(`- ${text(finding.code)} (${text(finding.confidence)}): ${text(finding.text)} Evidence: ${finding.evidenceIds.map(text).join(', ') || 'None'}.`);
  lines.push('', '## Cost provenance', '');
  for (const [kind, observation] of [['Measurement', report.cost.measurement], ['Estimate', report.cost.estimate]]) {
    lines.push(observation ? `- ${kind}: USD ${text(observation.amountUSD)}; admin recorded, provider verification: no. Source: ${text(observation.sourceKind)}, SHA-256 ${text(observation.sourceSha256)}. Window: ${stamp(observation.periodStart)} to ${stamp(observation.periodEnd)}; observed: ${stamp(observation.observedAt)}.` : `- ${kind}: unavailable. Missing data is not zero cost.`);
  }
  lines.push(text(report.cost.notice), '', '## Evidence references', '');
  for (const item of report.evidence) {
    const link = cloudWatchLink(item.logUrl, report.region);
    lines.push(`- ${text(item.id)}: ${text(item.kind)}${link ? ` — [Open returned log stream](${link})` : ''}`);
  }
  lines.push('', '## Snapshot dispositions', '', '| Snapshot ID | Disposition | Reason |', '| --- | --- | --- |');
  for (const row of report.rows) lines.push(`| ${text(row.id)} | ${text(row.disposition)} | ${text(row.reasonCode)} |`);
  lines.push('', '## Limitations', '', ...report.limitations.map(value => '- ' + text(value)), '',
    `Report JSON SHA-256: ${text(report.reportHash)}. This identifies the generated JSON; it is not a signature or protected audit archive.`, '');
  return lines.join('\n');
}

export function costPayload(form) {
  const result = Object.fromEntries(['kind', 'amountUSD', 'sourceKind', 'sourceSha256'].map(key => [key, form.get(key)]));
  if (!/^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?$/.test(result.amountUSD || '') || !/^[a-f0-9]{64}$/.test(result.sourceSha256 || ''))
    throw new Error('Use a nonnegative decimal USD amount and the reviewed source artifact SHA-256.');
  for (const key of ['periodStart', 'periodEnd', 'observedAt']) {
    const value = new Date(form.get(key)).getTime() / 1000;
    if (!Number.isFinite(value)) throw new Error('Choose valid cost observation times.');
    result[key] = Math.floor(value);
  }
  if (!(result.periodStart < result.periodEnd && result.periodEnd <= result.observedAt)) throw new Error('Cost times must be ordered: start, end, then observation.');
  return result;
}
