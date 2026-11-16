import React, { useEffect, useRef, useState } from 'react';
import { cloudWatchLink, localTimeInput } from './evidence.js';
import { duration } from './retention.js';
import { costPayload, reportMarkdown } from './report.js';

const stamp = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : 'Not established';
function download(value, filename, type) {
  const url = URL.createObjectURL(new Blob([value], { type }));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function CostForm({ value, incidentId, mutate, busy }) {
  const [kind, setKind] = useState('ESTIMATED'); const [error, setError] = useState('');
  return <details><summary>Record a cost observation · Admin</summary><form onSubmit={async event => {
    event.preventDefault(); setError('');
    try { await mutate('/incidents/' + incidentId + '/cost-observations', costPayload(new FormData(event.currentTarget))); }
    catch (problem) { setError(problem.message); }
  }}><p>Record an allocation from a reviewed source artifact. The application stores its hash and observation window; it does not independently validate the AWS bill or calculate a final incident total.</p>
    <label>Observation kind<select name="kind" value={kind} onChange={event => setKind(event.target.value)}><option value="ESTIMATED">Estimate</option><option value="MEASURED">Billing measurement (admin recorded)</option></select></label>
    <label>Amount (USD)<input name="amountUSD" inputMode="decimal" required placeholder="e.g. 0.0123" /></label>
    <label>Source kind<select name="sourceKind" key={kind}>{kind === 'MEASURED' ? <option value="AWS_BILLING_EXPORT">AWS billing export</option> : <><option value="OFFLINE_CALCULATOR">Offline calculator</option><option value="AWS_USAGE_EXPORT">AWS usage export with estimated rates</option><option value="MANUAL_ESTIMATE">Manual estimate</option></>}</select></label>
    <label>Source artifact SHA-256<input name="sourceSha256" required pattern="[a-f0-9]{64}" maxLength={64} /></label>
    <div className="cloud-evidence-window"><label>Cost window start<input name="periodStart" type="datetime-local" step="1" required defaultValue={localTimeInput(Math.min(value.incident.windowStart, value.generatedAt - 3600))} /></label><label>Cost window end<input name="periodEnd" type="datetime-local" step="1" required defaultValue={localTimeInput(value.generatedAt)} /></label><label>Source observed at<input name="observedAt" type="datetime-local" step="1" required defaultValue={localTimeInput(value.generatedAt)} /></label></div>
    {error && <p role="alert">{error}</p>}<button className="secondary" disabled={busy}>Record observation</button></form></details>;
}

export function IncidentReport({ client, incidentId, revision, report: onError, actor, mutate, busy, region }) {
  const [value, setValue] = useState(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [sample, setSample] = useState(''); const ticket = useRef(0);
  const refresh = async (query = {}) => {
    const request = ++ticket.current; setLoading(true); setError('');
    if (!Object.keys(query).length) setSample('');
    try { const result = await client.get('/incidents/' + incidentId + '/report', query); if (request === ticket.current) setValue(result); }
    catch (problem) { if (request === ticket.current) { setError(problem.message); if (problem.status === 401) onError(problem); } }
    finally { if (request === ticket.current) setLoading(false); }
  };
  useEffect(() => { refresh(); return () => { ticket.current++; }; }, [client, incidentId, revision]);
  return <section className="cloud-incident-report" aria-label="Incident report"><div className="cloud-report-actions"><button className="secondary" disabled={loading || busy} onClick={() => refresh()}>Refresh report</button>
    <button className="secondary" disabled={!value || loading || !!error} onClick={() => download(JSON.stringify(value, null, 2), 'incident-' + incidentId + '.json', 'application/json')}>Export report JSON</button>
    <button className="secondary" disabled={!value || loading || !!error} onClick={() => download(reportMarkdown(value), 'incident-' + incidentId + '.md', 'text/markdown')}>Export report Markdown</button></div>
    {loading && <p role="status">Reading bounded incident history…</p>}{error && <p role="alert">{error}</p>}
    {value && <><h3>{value.incident.label}</h3><p>{value.summary}</p><p>Collection window: {stamp(value.incident.windowStart)} – {stamp(value.incident.windowEnd)} · consumer version {value.incident.consumerVersion}.</p><p>Generated {stamp(value.generatedAt)} · indexed incident sample · original-message presence unverified.</p>
      {!value.coverage.completeForIndexedHistory && <div className="inline-note amber-note">Report coverage is incomplete: {value.coverage.issues.map(code => code.replaceAll('_', ' ').toLowerCase()).join('; ')}.</div>}
      <div className="plan-counters">{Object.entries(value.counts).map(([key, count]) => <div key={key}><strong>{count}</strong><span>{key}</span></div>)}</div>
      <p>{value.coverage.representedSnapshots} snapshots represented of {value.coverage.indexedSnapshotCount} indexed. Counts concern snapshots, not numbers of shipments or the full DLQ.</p>
      <h3>Business-verification coverage</h3><p>{value.verification.completeOperations} of {value.verification.attemptedOperations} distinct attempted operations have recorded COMPLETE evidence ({value.verification.completePercent === null ? 'not applicable' : value.verification.completePercent + '%'}). {value.verification.checkedOperations} have recorded checks. No live business verifier was invoked.</p>
      <h3>Recorded replay-job duration</h3><p>Started: {stamp(value.duration.startedAt)}. Recorded job resolution: {stamp(value.duration.resolvedAt)}.</p><p>Resolution duration: {duration(value.duration.resolvedDurationSeconds)}. Elapsed since start: {duration(value.duration.elapsedSeconds)}. Paused time and shared-job work are included. This interval does not establish full incident resolution; missing endpoints remain unknown.</p>
      <h3>Evidence-backed findings</h3>{value.hypotheses.map(finding => <div className="cloud-report-finding" key={finding.code}><strong>{finding.code.replaceAll('_', ' ')}</strong><p>{finding.text}</p><small>{finding.confidence.replaceAll('_', ' ')} · {finding.evidenceIds.length} evidence references</small></div>)}
      <h3>Optional correlated log sample</h3><form onSubmit={event => { event.preventDefault(); refresh(sample ? JSON.parse(sample) : {}); }}><label>Log evidence sample<select value={sample} onChange={event => setSample(event.target.value)}><option value="">No log lookup</option>{value.rows.flatMap(row => [<option key={row.id} value={JSON.stringify({ evidenceMessageId: row.id })}>Source {row.id.slice(0, 12)}</option>, ...row.attempts.map(attempt => <option key={attempt.id} value={JSON.stringify({ evidenceMessageId: row.id, jobId: attempt.jobId, attemptId: attempt.id })}>Attempt {attempt.id}</option>)])}</select></label><button className="secondary" disabled={loading || busy || !sample}>Include selected log sample</button></form>
      <p>Log lookup: {value.logLookup.status.replaceAll('_', ' ')}. A selected sample is not complete incident-wide log coverage and does not change recovery counts.</p>
      <h3>Cost provenance</h3>{[['Measurement', value.cost.measurement], ['Estimate', value.cost.estimate]].map(([label, cost]) => <div key={label}><strong>{label}: {cost ? 'USD ' + cost.amountUSD + ' (admin recorded)' : 'unavailable'}</strong>{cost && <p>{cost.sourceKind.replaceAll('_', ' ')} · window {stamp(cost.periodStart)} – {stamp(cost.periodEnd)} · provider verification: no</p>}</div>)}<p>{value.cost.notice}</p>{value.cost.historyTruncated && <p>Older cost observations were outside the bounded read.</p>}
      {actor.groups.includes('Admin') && <CostForm value={value} incidentId={incidentId} mutate={mutate} busy={busy || loading} />}
      <h3>Snapshot dispositions</h3><div className="table-scroll"><table><thead><tr><th>Snapshot ID</th><th>Disposition</th><th>Recorded basis</th></tr></thead><tbody>{value.rows.map(row => <tr key={row.id}><td className="mono">{row.id}</td><td>{row.disposition}</td><td>{row.reasonCode.replaceAll('_', ' ')}</td></tr>)}</tbody></table></div>
      <details><summary>Evidence references ({value.evidence.length})</summary><ul>{value.evidence.map(item => { const link = cloudWatchLink(item.logUrl, region); return <li key={item.id}><code>{item.id}</code> · {item.kind.replaceAll('_', ' ')}{link && <> · <a href={link} target="_blank" rel="noopener noreferrer">Open log stream</a></>}</li>; })}</ul></details>
      <details><summary>Scope and limitations</summary><ul>{value.limitations.map(text => <li key={text}>{text}</li>)}</ul><p className="mono">JSON SHA-256: {value.reportHash}</p><p>The hash identifies this JSON snapshot; it is not a signature or protected audit archive.</p></details>
    </>}
  </section>;
}
