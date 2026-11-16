import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, Check, CheckCheck, ChevronRight, FileCheck2, Fingerprint, Inbox, Layers3, LoaderCircle, LockKeyhole, LogOut, Menu, Pause, RefreshCw, Search, ShieldCheck, Square, TriangleAlert, Unplug, X } from 'lucide-react';
import { createAuth } from './auth.js';
import { createClient } from './client.js';
import './cloud.css';

const pages = [['overview', Layers3, 'Overview'], ['inbox', Inbox, 'Failure inbox'], ['plans', FileCheck2, 'Replay plans'], ['jobs', Activity, 'Recovery jobs'], ['audit', ShieldCheck, 'Audit trail'], ['connections', Unplug, 'Connections']];
const stamp = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : '—';
const endpoint = (path, id) => path + '/' + encodeURIComponent(id);
const color = state => ['VERIFIED', 'APPROVED', 'COMPLETED', 'ELIGIBLE', 'VALID'].includes(state) ? 'green' : ['BLOCKED', 'FAILED', 'INVALID', 'CANCELLED'].includes(state) ? 'red' : ['RUNNING', 'SENT', 'VERIFYING'].includes(state) ? 'blue' : 'amber';
function Badge({ children }) { return <span className={'badge ' + color(children)}><i />{String(children).replaceAll('_', ' ')}</span>; }
function Title({ title, text, children }) { return <div className="section-title"><div><div className="eyebrow">AWS RECOVERY WORKSPACE</div><h1>{title}</h1><p>{text}</p></div><div className="title-actions">{children}</div></div>; }
function Empty({ children }) { return <div className="empty"><Inbox size={28} /><p>{children}</p></div>; }
function Dialog({ title, close, children }) {
  useEffect(() => { const listener = event => { if (event.key === 'Escape') close(); }; addEventListener('keydown', listener); const old = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { removeEventListener('keydown', listener); document.body.style.overflow = old; }; }, []);
  return <div className="modal-backdrop" onClick={close}><section className="modal wide" role="dialog" aria-modal="true" aria-label={title} onClick={event => event.stopPropagation()}><div className="modal-heading"><h2>{title}</h2><button autoFocus className="icon-button" aria-label="Close dialog" onClick={close}><X size={20} /></button></div><div className="form-body">{children}</div></section></div>;
}
function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function usePage(client, path, revision, report, params = {}) {
  const scope = JSON.stringify([path, params]);
  const [pagination, setPagination] = useState({ scope, cursors: [null], index: 0 });
  if (pagination.scope !== scope) setPagination({ scope, cursors: [null], index: 0 });
  const { cursors, index } = pagination.scope === scope ? pagination : { cursors: [null], index: 0 };
  const requestKey = JSON.stringify([scope, revision, index, cursors[index]]);
  const [state, setState] = useState(null);
  useEffect(() => {
    let alive = true;
    setState({ key: requestKey, loading: true });
    const [route, filters] = JSON.parse(scope);
    client.get(route, { ...filters, limit: 25, cursor: cursors[index] }).then(result => { if (alive) setState({ key: requestKey, result }); }).catch(problem => { if (alive) { setState({ key: requestKey, error: problem.message }); report(problem); } });
    return () => { alive = false; };
  }, [client, requestKey]);
  const current = state?.key === requestKey ? state : null;
  const loading = !current || !!current.loading;
  const next = () => { if (current?.result?.nextCursor && !loading) setPagination({ scope, cursors: [...cursors.slice(0, index + 1), current.result.nextCursor], index: index + 1 }); };
  return { result: current?.result, loading, error: current?.error, index, next,
    previous: () => setPagination({ scope, cursors, index: Math.max(0, index - 1) }) };
}
function Pagination({ data }) { return <div className="pagination"><span>Page {data.index + 1} · {data.result?.items?.length || 0} records on this page</span><div><button className="secondary" disabled={data.loading || !data.index} onClick={data.previous}>Previous</button><button className="secondary" disabled={data.loading || !data.result?.nextCursor} onClick={data.next}>Next</button></div></div>; }
function useDetail(client, path, id, revision, report) {
  const [value, setValue] = useState(null);
  useEffect(() => {
    let alive = true; setValue(null);
    if (id) client.get(endpoint(path, id)).then(result => { if (alive) setValue(result); }).catch(problem => { if (alive) report(problem); });
    return () => { alive = false; };
  }, [client, path, id, revision]);
  return value;
}

export default function CloudApp({ config }) {
  const auth = useMemo(() => createAuth(config), [config]);
  const client = useMemo(() => createClient(config, auth), [config, auth]);
  const [actor, setActor] = useState(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const expire = useCallback(() => { auth.clear(); setActor(null); setError('Your session ended. Sign in again to continue.'); }, [auth]);
  useEffect(() => {
    let alive = true;
    auth.complete(location.href, url => history.replaceState(null, '', url)).then(async session => {
      if (!session) return;
      const identity = await client.get('/session');
      if (typeof identity.id !== 'string' || !Array.isArray(identity.groups)) throw new Error('The server identity could not be verified.');
      if (alive) setActor(identity);
    }).catch(problem => { auth.clear(); if (alive) setError(problem.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [auth, client]);
  useEffect(() => {
    if (!actor) return;
    const session = auth.session();
    if (!session) { expire(); return; }
    const timer = setTimeout(expire, Math.max(0, session.expiresAt - Date.now() - 5000));
    return () => clearTimeout(timer);
  }, [actor, auth, expire]);
  if (!actor) return <div className="cloud-login"><section className="panel cloud-login-card"><div className="brand"><span className="brand-mark"><Layers3 size={23} /></span>Queue<span>Rescue</span></div><span className="eyebrow">AWS SANDBOX · {config.region}</span><h1>Sign in to your recovery workspace.</h1><p>Use your application account to investigate failures, review plans, and verify recoveries.</p>{error && <div role="alert" className="inline-note amber-note"><TriangleAlert size={18} />{error}</div>}<button className="primary" disabled={loading} onClick={async () => { setLoading(true); try { location.assign(await auth.begin()); } catch { setError('Sign-in could not start. Please try again.'); setLoading(false); } }}>{loading ? <LoaderCircle className="spin" size={18} /> : <LockKeyhole size={18} />}{loading ? 'Checking sign-in…' : 'Sign in with Cognito'}</button><button className="secondary" disabled={loading} onClick={() => location.assign(auth.logoutUrl())}>Use a different application account</button><p className="muted">Operator and Approver are separate accounts. Your AWS root or IAM login is not an application login.</p></section></div>;
  return <CloudWorkspace config={config} client={client} actor={actor} onExpired={expire} onSignOut={() => location.assign(auth.logoutUrl())} />;
}

export function CloudWorkspace({ config, client, actor, onExpired, onSignOut }) {
  const selectionKey = 'qr-aws:' + config.binding + ':view';
  let savedView;
  try { const value = JSON.parse(sessionStorage.getItem(selectionKey) || '{}'); savedView = value && typeof value === 'object' ? value : {}; } catch { savedView = {}; }
  const initial = location.hash.slice(1) || savedView.page;
  const [page, setPage] = useState(pages.some(([key]) => key === initial) ? initial : 'overview');
  const [revision, setRevision] = useState(0); const [notice, setNotice] = useState(null); const [busy, setBusy] = useState(false);
  const [mobile, setMobile] = useState(false); const [selectedPlan, setSelectedPlan] = useState(savedView.plan || null); const [selectedJob, setSelectedJob] = useState(savedView.job || null);
  useEffect(() => { sessionStorage.setItem(selectionKey, JSON.stringify({ page, plan: selectedPlan, job: selectedJob })); }, [page, selectedPlan, selectedJob, selectionKey]);
  const canOperate = actor.groups.some(group => ['Operator', 'Admin'].includes(group));
  const canApprove = actor.groups.some(group => ['Approver', 'Admin'].includes(group));
  const go = key => { location.hash = key; setPage(key); setMobile(false); };
  useEffect(() => { const change = () => { const key = location.hash.slice(1); setPage(pages.some(([id]) => id === key) ? key : 'overview'); }; addEventListener('hashchange', change); return () => removeEventListener('hashchange', change); }, []);
  const report = problem => { setNotice({ text: problem.message, error: true }); if (problem.status === 401) onExpired(); };
  let pending, pendingError;
  try { pending = client.pending(); } catch (problem) { pendingError = problem.message; }
  const afterWrite = (path, result) => {
    if (path === '/collections') { go('inbox'); setNotice({ text: `Collection returned ${result.indexed.length} snapshot IDs. Some may already be indexed. Originals were retained.` }); }
    else if (path === '/replay-plans' || path.endsWith('/approve')) { setSelectedPlan(result.id); go('plans'); setNotice({ text: 'Plan action recorded. Review the current server state below.' }); }
    else { setSelectedJob(result.id); go('jobs'); setNotice({ text: 'Job action recorded. Sending alone is not a verified recovery.' }); }
    setRevision(value => value + 1);
  };
  const mutate = async (path, body, retry = false) => {
    setBusy(true);
    try { const result = retry ? await client.retry(actor) : await client.write(path, body, actor); afterWrite(path, result); return result; }
    catch (problem) { report(problem); return null; }
    finally { setBusy(false); }
  };
  const shared = { client, revision, report, mutate, busy: busy || !!pending || !!pendingError, canOperate, canApprove, actor };
  return <div className="app-shell cloud-workspace"><aside className={'sidebar ' + (mobile ? 'open' : '')}><a className="brand" href="#overview" onClick={() => go('overview')}><span className="brand-mark"><Layers3 size={23} /></span>Queue<span>Rescue</span></a><button className="workspace" onClick={() => go('connections')}><span className="workspace-icon">S</span><span><strong>Shipment workspace</strong><small>AWS sandbox · {config.region}</small></span><ChevronRight size={16} /></button><div className="nav-label">WORKSPACE</div><nav>{pages.map(([key, Icon, label]) => <button key={key} className={page === key ? 'active' : ''} onClick={() => go(key)}><Icon size={18} /><span>{label}</span></button>)}</nav><div className="sidebar-bottom"><div className="environment-card"><ShieldCheck size={20} /><div><strong>Originals retained</strong><small>Explicit recovery actions</small></div></div><button onClick={onSignOut}><LogOut size={17} />Sign out / change account</button></div></aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumbs"><button className="mobile-toggle icon-button" aria-label="Open navigation" onClick={() => setMobile(!mobile)}><Menu size={20} /></button><span>Workspace</span><ChevronRight size={14} /><strong>{pages.find(([key]) => key === page)?.[2]}</strong></div><div className="topbar-right"><span className="simulation-tag cloud-mode-tag">AWS SANDBOX</span><span className="cloud-identity" title={actor.id}>{actor.groups.join(', ')}</span><button className="icon-button" aria-label="Sign out" onClick={onSignOut}><LogOut size={18} /></button></div></header><main>
      <div className="cloud-refresh-bar"><span>Manual refresh · no background polling</span><button className="secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} />Refresh view</button></div>
      {notice && <div role={notice.error ? 'alert' : 'status'} className={'inline-note cloud-notice ' + (notice.error ? 'amber-note' : '')}>{notice.error ? <TriangleAlert size={18} /> : <Check size={18} />}<span>{notice.text}</span><button className="icon-button" aria-label="Dismiss notice" onClick={() => setNotice(null)}><X size={16} /></button></div>}
      {pendingError && <div role="alert" className="inline-note amber-note">{pendingError}</div>}
      {pending && <section className="panel pending-request"><h2>Recover the saved request</h2><p>The result of <code>{pending.path}</code> needs confirmation. New actions are paused. Retry uses the original request key and body.</p><p className="muted">Request key: <code>{pending.requestKey}</code></p><button className="primary" disabled={busy || pending.subject !== actor.id} onClick={() => mutate(pending.path, null, true)}>Retry same request</button>{pending.subject !== actor.id && <p>Sign in as the original requester to resolve this action.</p>}</section>}
      {page === 'overview' && <><Title title="A deliberate path to recovery." text="Collect evidence, approve an exact plan, and confirm the business outcome." /><div className="overview-banner"><ShieldCheck size={30} /><div><strong>Connected with your application identity.</strong><p>QueueRescue reads server-authorized snapshots. Collection and recovery run only when you explicitly request them.</p></div></div><section className="panel cloud-steps">{[['inbox', '01', 'Investigate', 'Sample up to 10 DLQ messages and inspect masked snapshots.'], ['plans', '02', 'Plan and approve', 'Select up to 25 snapshot IDs; a separate account approves the exact hash.'], ['jobs', '03', 'Replay and verify', 'Control execution and verify the consumer’s business outcome.']].map(([key, step, title, text]) => <button key={key} onClick={() => go(key)}><span className="eyebrow">{step}</span><h2>{title}</h2><p>{text}</p><ArrowRight size={18} /></button>)}</section></>}
      {page === 'inbox' && <CloudInbox {...shared} />}
      {page === 'plans' && <CloudPlans {...shared} selectedId={selectedPlan} select={setSelectedPlan} />}
      {page === 'jobs' && <CloudJobs {...shared} selectedId={selectedJob} select={setSelectedJob} />}
      {page === 'audit' && <CloudAudit {...shared} />}
      {page === 'connections' && <CloudConnections {...shared} config={config} />}
      <footer className="page-footer"><span><ShieldCheck size={13} />AWS sandbox · originals retained</span><span>Business verification determines recovery</span></footer>
    </main></div></div>;
}

function InvestigationFilters({ apply }) {
  const [search, setSearch] = useState(''); const [category, setCategory] = useState('');
  const [schema, setSchema] = useState(''); const [hours, setHours] = useState('');
  return <form className="filter-bar cloud-investigation-filters" onSubmit={event => {
    event.preventDefault(); apply({ q: search, category, schema,
      ...(hours ? { observedFrom: String(Math.floor(Date.now() / 1000) - Number(hours) * 3600) } : {}) });
  }}><label className="search-field"><Search size={17} /><input aria-label="Search indexed evidence" placeholder="Search indexed IDs, operations, or categories…" maxLength={128} value={search} onChange={event => setSearch(event.target.value)} /></label>
    <select aria-label="Failure category" value={category} onChange={event => setCategory(event.target.value)}><option value="">All failure categories</option><option value="UNCLASSIFIED">Unclassified failure</option><option value="INVALID_SCHEMA">Invalid shipment schema</option></select>
    <select aria-label="Shipment schema" value={schema} onChange={event => setSchema(event.target.value)}><option value="">All schemas</option><option value="valid">Valid schema</option><option value="invalid">Invalid schema</option></select>
    <select aria-label="Collection time" value={hours} onChange={event => setHours(event.target.value)}><option value="">All collection times</option><option value="24">Last 24 hours</option><option value="2">Last 2 hours</option></select>
    <button className="secondary" type="submit">Apply filters</button></form>;
}

function CloudInbox({ client, revision, report, mutate, busy, canOperate }) {
  const [filters, setFilters] = useState({}); const [view, setView] = useState('messages'); const [incident, setIncident] = useState(null);
  const data = usePage(client, incident ? endpoint('/incidents', incident.id) + '/messages' : '/' + view, revision, report, filters);
  const [selected, setSelected] = useState([]); const [dialog, setDialog] = useState(null);
  const [ack, setAck] = useState(false); const [rate, setRate] = useState(5); const [message, setMessage] = useState(null);
  const toggle = id => setSelected(old => old.includes(id) ? old.filter(value => value !== id) : old.length < 25 ? [...old, id] : old);
  const inspect = async id => { try { setMessage(await client.get(endpoint('/messages', id))); } catch (problem) { report(problem); } };
  const visible = data.result?.items || [];
  const groups = view === 'incidents' && !incident;
  const changeView = next => { if (next === view && !incident) return; setView(next); setIncident(null); setFilters({}); };
  return <><Title title="Failure inbox" text="Indexed evidence from the configured dead-letter queue; this is a sample, not a complete queue inventory."><button className="secondary" disabled={!canOperate || busy} onClick={() => { setAck(false); setDialog('collect'); }}>Collect snapshots</button><button className="primary" disabled={!canOperate || busy || !selected.length} onClick={() => setDialog('plan')}><FileCheck2 size={16} />Plan {selected.length} selected</button></Title>
    <div className="cloud-investigation-toolbar"><button className="secondary" aria-pressed={view === 'messages'} onClick={() => changeView('messages')}>Snapshots</button><button className="secondary" aria-pressed={view === 'incidents'} onClick={() => changeView('incidents')}>Incident groups</button><span>{selected.length} / 25 selected across pages and filters</span><button className="text-button" onClick={() => setSelected([])}>Clear selection</button></div>
    {incident && <div className="inline-note"><span>{incident.label} · collected during {stamp(incident.windowStart)} – {stamp(incident.windowEnd)}</span><button className="text-button" onClick={() => changeView('incidents')}>All incident groups</button></div>}
    <InvestigationFilters key={view + (incident?.id || '')} apply={setFilters} />
    <p className="cloud-investigation-help">Filters search indexed metadata on the server. Groups use queue, schema category, consumer version and UTC collection hour; they do not establish a root cause. Counts reflect snapshots collected into each group, including members whose retention has since expired.</p>
    {data.result?.readBudgetReached && <div role="status" className="inline-note amber-note">The bounded search reached its read budget. Use Next to continue, even if this page has no matches.</div>}
    {groups ? <section className="panel"><div className="panel-heading"><h2>Incident groups</h2><span className="subtle-label">{data.loading ? 'Loading…' : 'Collected snapshots only'}</span></div><div className="table-scroll"><table><thead><tr><th>Failure category</th><th>Collected</th><th>Consumer version</th><th>Collection window</th></tr></thead><tbody>{visible.map(item => <tr key={item.id}><td><button className="row-link" onClick={() => { setIncident(item); setFilters({}); }}><strong>{item.label}</strong><small className="mono">{item.id}</small></button></td><td>{item.collectedCount}</td><td className="mono">{item.consumerVersion}</td><td>{stamp(item.windowStart)} – {stamp(item.windowEnd)}</td></tr>)}</tbody></table></div>{!data.loading && !visible.length && <Empty>{data.error ? 'Incident groups could not be loaded. Refresh to try again.' : 'No groups on this page. Continue with Next when available, or adjust filters.'}</Empty>}<Pagination data={data} /></section> : <>
    <section className="panel"><div className="panel-heading"><h2>Indexed snapshots</h2><span className="subtle-label">{data.loading ? 'Loading…' : 'Outcomes checked during planning'}</span></div><div className="table-scroll"><table><thead><tr><th>Select</th><th>Message / operation</th><th>Error</th><th>Schema</th><th>Observed</th><th /></tr></thead><tbody>{visible.map(item => <tr key={item.id}><td><input type="checkbox" aria-label={'Select ' + item.id} checked={selected.includes(item.id)} disabled={!canOperate || busy || data.loading || item.expires_at <= Date.now() / 1000 || (!selected.includes(item.id) && selected.length >= 25)} onChange={() => toggle(item.id)} /></td><td><button className="row-link" onClick={() => inspect(item.id)}><strong className="mono">{item.operationId || 'No valid operation ID'}</strong><small className="mono">{item.id}</small></button></td><td>{item.error}</td><td><Badge>{item.valid ? 'VALID' : 'INVALID'}</Badge></td><td>{stamp(item.observedAt)}</td><td><button className="icon-button" aria-label={'Inspect ' + item.id} onClick={() => inspect(item.id)}><Search size={17} /></button></td></tr>)}</tbody></table></div>{!data.loading && !visible.length && <Empty>{data.error ? 'Snapshots could not be loaded. Refresh to try again.' : 'No matching snapshots on this page. Collect evidence or browse another page.'}</Empty>}<Pagination data={data} /></section>
    </>}
    <div className="inline-note"><ShieldCheck size={17} />Valid schema does not mean eligible for replay. The business verifier decides during planning and checks again before sending.</div>
    {dialog === 'collect' && <Dialog title="Collect DLQ snapshots" close={() => setDialog(null)}><p>Sample up to ten messages from the configured DLQ and save encrypted evidence. Sampling can return messages that are already indexed.</p><label className="checkbox-label"><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />I understand that receiving temporarily changes message visibility. Original messages will not be deleted.</label><div className="modal-actions"><button className="secondary" onClick={() => setDialog(null)}>Cancel</button><button className="primary" disabled={!ack || busy} onClick={async () => { if (await mutate('/collections', { acknowledgeVisibilityChange: true })) setDialog(null); }}>Collect up to 10 snapshots</button></div></Dialog>}
    {dialog === 'plan' && <Dialog title="Create an AWS replay plan" close={() => setDialog(null)}><form onSubmit={async event => { event.preventDefault(); await mutate('/replay-plans', { messageIds: selected, rate: Number(rate) }); }}><p>Check exactly {selected.length} selected snapshot IDs. Planning sends no replay messages.</p><label>Maximum messages per second<input type="number" min="1" max="25" required value={rate} onChange={event => setRate(event.target.value)} /></label><p>The destination is fixed by the server. Approval expires after one hour.</p><details><summary>Selected snapshot IDs</summary><pre>{selected.join('\n')}</pre></details><div className="modal-actions"><button type="button" className="secondary" onClick={() => setDialog(null)}>Cancel</button><button className="primary" disabled={busy}>Generate dry run</button></div></form></Dialog>}
    {message && <Dialog title="Event investigation" close={() => setMessage(null)}><h3 className="mono">{message.operationId || message.id}</h3><div className="info-grid"><div><span>Original message ID</span><strong className="mono">{message.originalMessageId}</strong></div><div><span>Observed</span><strong>{stamp(message.observedAt)}</strong></div><div><span>Evidence retention expires</span><strong>{stamp(message.expires_at)}</strong></div><div><span>Original DLQ message</span><strong>Retained</strong></div></div><h3>Masked payload</h3><pre>{JSON.stringify(message.payload, null, 2)}</pre><h3>Body checksum</h3><div className="hash-display"><Fingerprint size={16} /><code>{message.checksum}</code></div><h3>Evidence checksum</h3><div className="hash-display"><Fingerprint size={16} /><code>{message.evidenceChecksum}</code></div><p className="muted">This view shows snapshot evidence. CloudWatch failure correlation is not available yet.</p></Dialog>}
  </>;
}

function Catalog({ data, selectedId, select, label }) { return <section className="panel plan-list"><div className="panel-heading"><h2>{label}</h2></div>{data.result?.items.map(item => <button key={item.id} className={'plan-list-item ' + (item.id === selectedId ? 'selected' : '')} onClick={() => select(item.id)}><strong className="mono">{item.id}</strong><small>Open server details<ChevronRight size={14} /></small></button>)}{!data.loading && !data.result?.items?.length && <Empty>{data.error ? 'Records could not be loaded.' : 'No records on this page.'}</Empty>}<Pagination data={{ ...data, next: () => { select(null); data.next(); }, previous: () => { select(null); data.previous(); } }} /></section>; }

function CloudPlans({ client, revision, report, mutate, busy, canOperate, canApprove, actor, selectedId, select }) {
  const data = usePage(client, '/replay-plans', revision, report);
  const id = selectedId || data.result?.items[0]?.id;
  const plan = useDetail(client, '/replay-plans', id, revision, report);
  const [note, setNote] = useState('');
  const expired = plan?.expiresAt <= Date.now() / 1000;
  const decisions = plan?.document.decisions || [];
  const counts = Object.fromEntries(['ELIGIBLE', 'SKIP', 'REVIEW', 'BLOCKED'].map(state => [state, decisions.filter(item => item.decision === state).length]));
  return <><Title title="Replay plans" text="Review the immutable document, business decisions, and exact approval hash." /><div className="split-layout"><Catalog data={data} selectedId={id} select={select} label="Saved plan IDs" />{plan ? <section className="panel plan-detail"><div className="panel-heading"><div><h2>Recovery preview <Badge>{expired && plan.state === 'READY' ? 'EXPIRED' : plan.state}</Badge></h2><p className="mono">{plan.document.id} · version {plan.document.version}</p></div><button className="icon-button" aria-label="Export plan" onClick={() => download(plan, id + '.json')}><ArrowDownToLine size={18} /></button></div><div className="plan-content"><div className="plan-counters">{Object.entries(counts).map(([state, count]) => <div key={state}><strong className={color(state) + '-text'}>{count}</strong><span>{state}</span></div>)}</div><div className="info-grid"><div><span>Controlled destination</span><strong className="mono">{plan.document.destination}</strong></div><div><span>Maximum send rate</span><strong>{plan.document.rate} messages / second</strong></div><div><span>Created by</span><strong className="mono">{plan.createdBy}</strong></div><div><span>Expires</span><strong>{stamp(plan.expiresAt)}</strong></div></div><h3>Exact plan hash</h3><div className="hash-display"><Fingerprint size={17} /><code>{plan.planHash}</code></div><h3>All selected decisions</h3><div className="decision-list">{decisions.map(item => <div key={item.messageId}><div><strong className="mono">{item.operationId || item.messageId}</strong><small>{item.reason}</small></div><Badge>{item.decision}</Badge></div>)}</div>
      {plan.state === 'READY' && <div className="approval-block"><h3><LockKeyhole size={18} />A separate approver is required</h3><p>Sign out and use the Approver account. The server compares identities and rejects self-approval.</p>{canApprove && <label>Approval note<textarea minLength={5} maxLength={1000} value={note} onChange={event => setNote(event.target.value)} /></label>}<button className="primary" disabled={busy || !canApprove || plan.createdBy === actor.id || expired || !counts.ELIGIBLE || note.trim().length < 5} onClick={() => mutate(endpoint('/replay-plans', id) + '/approve', { planHash: plan.planHash, note })}>Approve exact plan</button></div>}
      {plan.state === 'APPROVED' && <div className="approval-block"><h3><CheckCheck size={18} />Approved by <span className="mono">{plan.approvedBy}</span></h3><p>{plan.approvalNote}</p><button className="primary" disabled={busy || !canOperate || expired} onClick={() => mutate('/replay-jobs', { approvedPlanId: id })}>Start approved recovery</button>{expired && <p>Approval expired. Create a new plan.</p>}</div>}
      {plan.state === 'EXECUTING' && <p>Execution has started. Open Recovery jobs to inspect progress.</p>}
    </div></section> : <section className="panel"><Empty>{id ? 'Open or refresh a plan to load its server details.' : 'Select snapshots in the failure inbox to create a plan.'}</Empty></section>}</div></>;
}

function CloudJobs({ client, revision, report, mutate, busy, canOperate, selectedId, select }) {
  const data = usePage(client, '/replay-jobs', revision, report);
  const id = selectedId || data.result?.items[0]?.id;
  const job = useDetail(client, '/replay-jobs', id, revision, report);
  return <><Title title="Recovery jobs" text="Refresh deliberately to inspect execution and domain-confirmed outcomes." /><div className="cloud-job-catalog"><Catalog data={data} selectedId={id} select={select} label="Saved job IDs" /></div>{job && <><section className="panel job-header"><div><h2>{job.id}<Badge>{job.state}</Badge></h2><p>Started {stamp(job.createdAt)} · generation {job.generation}</p><p className="mono">Plan {job.planId}</p></div><div className="job-controls">{job.state === 'RUNNING' && <button className="secondary" disabled={!canOperate || busy} onClick={() => mutate(endpoint('/replay-jobs', id) + '/pause', { reason: 'Operator requested pause' })}><Pause size={15} />Pause</button>}{job.state === 'PAUSED' && <button className="primary" disabled={!canOperate || busy || job.counts.UNCERTAIN > 0 || job.counts.LEASED > 0} onClick={() => mutate(endpoint('/replay-jobs', id) + '/resume', { reason: 'Operator reviewed outcomes and requested resume' })}>Resume</button>}{!['COMPLETED', 'CANCELLED', 'FAILED'].includes(job.state) && <button className="danger-button" disabled={!canOperate || busy} onClick={() => mutate(endpoint('/replay-jobs', id) + '/cancel', { reason: 'Operator requested cancellation' })}><Square size={15} />Cancel</button>}<button className="primary" disabled={!canOperate || busy} onClick={() => mutate('/verifications', { jobId: id })}><CheckCheck size={16} />Verify / reconcile</button><button className="secondary" onClick={() => download(job, id + '-snapshot.json')}><ArrowDownToLine size={16} />Export job snapshot</button></div></section>{job.reason && <div className="inline-note amber-note">{job.reason}</div>}<div className="job-stats">{['QUEUED', 'SENT', 'VERIFIED', 'UNCERTAIN', 'UNVERIFIED', 'REVIEW'].map(state => <div key={state}><strong>{job.counts[state] || 0}</strong><span>{state}</span></div>)}</div><section className="panel"><div className="panel-heading"><h2>Replay ledger</h2><span>{job.counts.VERIFIED || 0} / {job.total} business outcomes verified</span></div><div className="table-scroll"><table><thead><tr><th>Operation</th><th>Attempt ID</th><th>State</th><th>Outcome / evidence</th></tr></thead><tbody>{job.attempts.map(attempt => <tr key={attempt.id}><td className="mono">{attempt.operationId}</td><td className="mono">{attempt.id}</td><td><Badge>{attempt.state}</Badge></td><td>{attempt.outcome || attempt.detail || 'No business completion proof recorded'}</td></tr>)}</tbody></table></div></section><div className="inline-note"><ShieldCheck size={17} />Accepted sends are not recoveries. Original DLQ messages remain retained.</div></>}</>;
}

function CloudAudit({ client, revision, report }) {
  const data = usePage(client, '/audit', revision, report);
  return <><Title title="Audit trail" text="Application audit records from AWS. This sandbox does not claim an independently protected hash chain."><button className="secondary" disabled={!data.result} onClick={() => download(data.result, 'aws-audit-page.json')}><ArrowDownToLine size={16} />Export this page</button></Title><section className="panel"><div className="table-scroll"><table><thead><tr><th>Time / actor</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead><tbody>{data.result?.items.map(item => <tr key={item.sk}><td><strong>{stamp(item.at)}</strong><small className="mono">{item.actor}</small></td><td>{item.action.replaceAll('_', ' ')}</td><td className="mono">{item.resource}</td><td><details><summary>Recorded evidence</summary><pre>{JSON.stringify(item.detail, null, 2)}</pre></details></td></tr>)}</tbody></table></div>{!data.loading && !data.result?.items?.length && <Empty>{data.error ? 'Audit records could not be loaded.' : 'No audit records on this page.'}</Empty>}<Pagination data={data} /></section></>;
}

function CloudConnections({ client, revision, report, config }) {
  const [connection, setConnection] = useState(null);
  useEffect(() => { let alive = true; client.get('/connections').then(result => { if (alive) setConnection(result.items[0]); }).catch(problem => { if (alive) report(problem); }); return () => { alive = false; }; }, [client, revision]);
  return <><Title title="Connections" text="Server-configured destinations; browser users cannot choose an arbitrary replay queue." /><section className="panel"><div className="panel-heading"><h2>AWS sandbox connection</h2><Badge>{connection?.mode || 'LOADING'}</Badge></div><div className="cloud-connection-content"><div className="info-grid">{[['API endpoint', config.apiUrl], ['Application sign-in', config.loginUrl], ['Region', connection?.region], ['Source DLQ', connection?.dlqUrl], ['Controlled replay destination', connection?.destination], ['Consumer version', connection?.consumerVersion]].map(([label, value]) => <div key={label}><span>{label}</span><strong className="mono">{value || 'Not loaded'}</strong></div>)}</div><div className="inline-note"><ShieldCheck size={17} />Authentication is enforced by Cognito and the API. Worker availability and account cost eligibility must be confirmed by the deployment operator.</div></div></section></>;
}
