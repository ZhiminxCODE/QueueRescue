import React, { useEffect, useRef, useState } from 'react';
import { cloudWatchLink, evidenceWindow, localTimeInput } from './evidence.js';

const sourceStatus = { COMPLETE: 'Queried window complete', TRUNCATED: 'Read limit reached', DENIED: 'Access denied',
  MISSING: 'Log group missing', UNAVAILABLE: 'Lookup unavailable', NOT_CONFIGURED: 'Lookup not configured' };

export function EvidenceLookup({ client, identifier, query = {}, region, report }) {
  const [result, setResult] = useState(null); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [from, setFrom] = useState(''); const [to, setTo] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const lookup = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setLoading(true); setError(''); setResult(null);
    try {
      const value = await client.get('/messages/' + encodeURIComponent(identifier) + '/evidence', {
        ...query, ...evidenceWindow(form.get('from'), form.get('to')),
      });
      if (alive.current) { setResult(value); setFrom(localTimeInput(value.from)); setTo(localTimeInput(value.to)); }
    } catch (problem) { if (alive.current) { setError(problem.message); if (problem.status === 401) report(problem); } }
    finally { if (alive.current) setLoading(false); }
  };
  return <section className="cloud-log-evidence" aria-label="CloudWatch evidence"><h3>CloudWatch evidence</h3>
    <p>Look up diagnostic logs for this {query.attemptId ? 'exact replay attempt' : 'source message'}. Lookup runs only when requested. Logs do not establish business recovery.</p>
    <form onSubmit={lookup}><div className="cloud-evidence-window"><label>Window start (local time)<input name="from" type="datetime-local" step="1" value={from} onChange={event => setFrom(event.target.value)} /></label><label>Window end (local time)<input name="to" type="datetime-local" step="1" value={to} onChange={event => setTo(event.target.value)} /></label></div><p className="muted">Leave times empty for the default window. Each lookup covers at most 24 hours, with up to two pages per configured log group.</p><button className="secondary" type="submit" disabled={loading}>{loading ? 'Looking up logs…' : 'Look up logs'}</button></form>
    {error && <p role="alert">{error}</p>}
    {result && <div aria-live="polite"><h3>{result.items.length ? 'Correlated telemetry found' : 'No correlated telemetry found'}</h3>
      <p>{result.failureObserved ? 'Failure telemetry was observed.' : 'No failure telemetry was returned.'} Missing logs do not prove success. Logs may be delayed, expired, unavailable, or outside the searched window.</p>
      {!result.searchComplete && <p className="inline-note amber-note">Evidence is incomplete. Review lookup coverage and time bounds before drawing conclusions.</p>}
      {result.windowClipped && <p>The selected window covers only part of the resource lifetime. Choose another window of at most 24 hours to inspect earlier evidence.</p>}
      <ul>{result.sources.map(source => <li key={source.component}><strong>{source.component}</strong>: {sourceStatus[source.status] || 'Inconclusive'} · {source.pagesRead} pages read</li>)}</ul>
      <div className="decision-list">{result.items.map((item, index) => {
        const link = cloudWatchLink(item.logUrl, region);
        return <div key={index}><div><strong>{item.event.replaceAll('_', ' ')}</strong><small>{new Date(item.observedAt).toLocaleString()} · {item.component}{item.outcome ? ' · ' + item.outcome.replaceAll('_', ' ') : ''}</small></div>{link && <a href={link} target="_blank" rel="noopener noreferrer">Open log stream</a>}</div>;
      })}</div>
      <details><summary>Hashed correlation references</summary><pre>{JSON.stringify(result.correlation, null, 2)}</pre></details>
      <p>Distributed traces are not instrumented. This view links only to log streams returned by the lookup. Use business verification to assess recovery.</p>
    </div>}
  </section>;
}
