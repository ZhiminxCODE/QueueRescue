import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, Bell, Boxes, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, Database, FileCheck2, FileText, Fingerprint, GitBranch, Inbox, Layers3, LayoutDashboard, ListFilter, LoaderCircle, LockKeyhole, Menu, Pause, Play, Plus, Radio, RefreshCw, Search, ShieldCheck, ShieldX, Square, Terminal, TriangleAlert, Unplug, X } from 'lucide-react';
import { api, download, login, query } from './api';
import CloudApp from './cloud/CloudApp.jsx';
import { dashboardConfig } from './cloud/config.js';
import './styles.css';

const fmt = n => new Intl.NumberFormat().format(n || 0);
const date = n => new Date(n * 1000).toLocaleString();
const age = n => { const m = Math.max(1, Math.floor((Date.now() / 1000 - n) / 60)); return m < 60 ? m + 'm' : Math.floor(m / 60) + 'h ' + m % 60 + 'm'; };
const names = { overview: 'Overview', inbox: 'Failure inbox', investigate: 'Investigate', plans: 'Replay plans', jobs: 'Recovery jobs', audit: 'Audit trail', connections: 'Connections' };
const navigation = [['overview', LayoutDashboard], ['inbox', Inbox], ['investigate', Search], ['plans', FileCheck2], ['jobs', Activity], ['audit', ShieldCheck]];
const stateColor = s => ['COMPLETE', 'COMPLETED', 'VERIFIED', 'APPROVED', 'ELIGIBLE', 'VALID'].includes(s) ? 'green' : ['RUNNING', 'VERIFYING', 'SENT', 'READY', 'LEASED'].includes(s) ? 'blue' : ['BLOCKED', 'FAILED', 'CANCELLED', 'INVALID'].includes(s) ? 'red' : ['SKIP', 'SKIPPED', 'QUEUED'].includes(s) ? 'gray' : 'amber';

function Badge({ children, color }) { return <span className={`badge ${color || stateColor(children)}`}><i />{String(children).replaceAll('_', ' ')}</span>; }
function Empty({ icon: Icon = Inbox, title, text, action }) { return <div className="empty"><Icon size={30} /><h3>{title}</h3><p>{text}</p>{action}</div>; }
function Stat({ title, value, icon: Icon, detail, tone = '' }) { return <div className={'stat ' + tone}><div className="stat-top"><span>{title}</span><Icon size={17} /></div><strong>{fmt(value)}</strong><small>{detail}</small></div>; }
function SectionTitle({ eyebrow, title, text, children }) { return <div className="section-title"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{text && <p>{text}</p>}</div><div className="title-actions">{children}</div></div>; }
function PageButtons({ cursor, next, setCursor, total }) { return <div className="pagination"><span>{fmt(total)} records · showing {total ? cursor + 1 : 0}–{Math.min(cursor + 25, total)}</span><div><button disabled={!cursor} className="icon-button" aria-label="Previous page" onClick={() => setCursor(Math.max(0, cursor - 25))}><ChevronRight className="rotate" size={17} /></button><button disabled={!next} className="icon-button" aria-label="Next page" onClick={() => setCursor(Number(next))}><ChevronRight size={17} /></button></div></div>; }

function App() {
  const [page, setPage] = useState(location.hash.slice(1) || 'overview');
  const [actor, setActor] = useState(null);
  const [summary, setSummary] = useState(null);
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState(null);
  const [seedFilter, setSeedFilter] = useState('');
  const [mobileNav, setMobileNav] = useState(false);
  const [help, setHelp] = useState(false);

  const navigate = p => { setPage(p); location.hash = p; setMobileNav(false); };
  const refresh = () => setVersion(v => v + 1);
  const notify = (text, error = false) => setNotice({ text, error });
  const act = async (work, message) => {
    setBusy(true);
    try { const result = await work(); if (message) notify(message); refresh(); return result; }
    catch (e) { notify(e.message, true); return null; }
    finally { setBusy(false); }
  };
  const canOperate = ['Operator', 'Admin'].includes(actor?.role);
  const canApprove = ['Approver', 'Admin'].includes(actor?.role);
  useEffect(() => {
    login(sessionStorage.getItem('qr-role') || 'Operator').then(setActor).catch(e => notify(e.message, true));
    const handler = () => setPage(location.hash.slice(1) || 'overview');
    addEventListener('hashchange', handler);
    return () => removeEventListener('hashchange', handler);
  }, []);
  useEffect(() => {
    if (!actor) return;
    let alive = true;
    const load = () => api('/overview').then(s => { if (alive) setSummary(s); }).catch(e => { if (alive) notify(e.message, true); });
    load(); const timer = setInterval(load, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [actor, version]);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(null), notice.error ? 10000 : 5500); return () => clearTimeout(t); }, [notice]);
  const inspect = async id => { try { setDetail(await api('/messages/' + id)); } catch (e) { notify(e.message, true); } };
  const openInbox = error => { setSeedFilter(error || ''); navigate('inbox'); };
  const shared = { actor, summary, version, refresh, act, busy, notify, canOperate, canApprove, navigate, inspect, openInbox };

  return <div className="app-shell">
    <aside className={'sidebar ' + (mobileNav ? 'open' : '')}>
      <a className="brand" href="#overview" onClick={() => navigate('overview')}><span className="brand-mark"><Layers3 size={23} /></span>Queue<span>Rescue</span></a>
      <button className="workspace" onClick={() => navigate('connections')}><span className="workspace-icon">S</span><span><strong>Shipment workspace</strong><small>Local environment</small></span><ChevronDown size={14} /></button>
      <div className="nav-label">WORKSPACE</div>
      <nav>{navigation.map(([id, Icon]) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={18} /><span>{names[id]}</span>{id === 'inbox' && summary && <em>{fmt(summary.total)}</em>}{id === 'jobs' && summary?.activeJobs > 0 && <span className="nav-dot" />}</button>)}</nav>
      <div className="sidebar-bottom"><button className={page === 'connections' ? 'active' : ''} onClick={() => navigate('connections')}><Unplug size={17} />Connections</button><button onClick={() => setHelp(true)}><CircleHelp size={17} />Prototype guide<ArrowUpRight size={14} /></button><div className="environment-card"><span className="live-dot" /><div><strong>Safe by default</strong><small>Original messages retained</small></div><ShieldCheck size={19} /></div><div className="sidebar-footer"><span className="mini-logo">Q</span>QueueRescue<span>v0.1</span></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumbs"><button className="mobile-toggle icon-button" aria-label="Open navigation" onClick={() => setMobileNav(!mobileNav)}><Menu size={19} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{names[page] || 'Overview'}</strong></div><div className="topbar-right"><span className="simulation-tag"><span className="live-dot" />LOCAL SIMULATION</span><span className="region">us-east-1</span><button className="icon-button" aria-label="View audit notifications" onClick={() => navigate('audit')}><Bell size={17} /></button><div className="identity"><div className="avatar">{actor?.name?.split(' ').map(s => s[0]).join('') || '…'}</div><select aria-label="Demo identity" value={actor?.role || 'Operator'} onChange={e => act(async () => { setActor(await login(e.target.value)); }, 'Demo identity changed. Permissions are enforced by the API.')}><option>Operator</option><option>Approver</option><option>Viewer</option><option>Admin</option></select></div></div></header>
      <main>
        {!actor || !summary ? <div className="loading"><LoaderCircle className="spin" />Connecting to the local recovery workspace…{notice?.error && <p>{notice.text}</p>}</div> : <>
          {page === 'overview' && <Overview {...shared} />}
          {page === 'inbox' && <InboxPage {...shared} initialFilter={seedFilter} />}
          {page === 'investigate' && <InboxPage {...shared} initialFilter="" investigation />}
          {page === 'plans' && <Plans {...shared} />}
          {page === 'jobs' && <Jobs {...shared} />}
          {page === 'audit' && <Audit {...shared} />}
          {page === 'connections' && <Connections {...shared} />}
          {!names[page] && <Empty title="Page not found" text="Return to your recovery overview." action={<button className="primary" onClick={() => navigate('overview')}>Open overview</button>} />}
        </>}
        <footer className="page-footer"><span><ShieldCheck size={13} />Customer-controlled recovery. Every action leaves a trail.</span><span>Synthetic shipment data · Prototype</span></footer>
      </main>
    </div>
    {notice && <div role="status" className={'toast ' + (notice.error ? 'error' : '')}>{notice.error ? <TriangleAlert size={18} /> : <Check size={18} />}<span>{notice.text}</span><button aria-label="Dismiss notification" onClick={() => setNotice(null)}><X size={16} /></button></div>}
    {detail && <MessageDrawer message={detail} close={() => setDetail(null)} create={() => { setDetail(null); navigate('plans'); }} />}
    {help && <Modal title="Your first recovery" close={() => setHelp(false)}><div className="guide"><p>This local prototype uses synthetic events and a simulated shipment service. No AWS connection is active.</p>{['Inspect a failure group and its masked evidence.', 'As Operator, create a dry-run replay plan.', 'Switch to Approver and approve the exact plan hash.', 'Switch back to Operator and start recovery.', 'Verify business outcomes in Jobs. For the interruption demo, reconcile, resume, then verify again.'].map((s, i) => <div className="guide-step" key={s}><span>{i + 1}</span>{s}</div>)}<p className="muted">Demo identities let you explore server-enforced roles. They are not production authentication. Original snapshots are always retained.</p></div></Modal>}
  </div>;
}

function Overview({ summary, openInbox, navigate, version, inspect }) {
  const [activity, setActivity] = useState([]);
  useEffect(() => { api('/audit?limit=4').then(r => setActivity(r.items)).catch(() => {}); }, [version]);
  const eligible = summary.groups.reduce((a, g) => a + g.ELIGIBLE, 0);
  const review = summary.groups.reduce((a, g) => a + g.REVIEW, 0);
  return <>
    <SectionTitle eyebrow="RECOVERY WORKSPACE" title="A clear path from failure to recovery." text="Investigate the evidence. Replay with confidence. Verify the outcome."><button className="secondary" onClick={() => navigate('connections')}><Unplug size={16} />Connections</button><button className="primary" onClick={() => openInbox('')}>Open failure inbox<ArrowRight size={16} /></button></SectionTitle>
    <div className="overview-banner"><div className="banner-icon"><ShieldCheck size={26} /></div><div><strong>Recovery starts with knowing what’s safe.</strong><p>{fmt(eligible)} events are eligible for replay. {fmt(review)} need a closer look before any action.</p></div><button onClick={() => openInbox('WarehouseTimeout')}>Review failures<ArrowUpRight size={16} /></button><div className="banner-pattern" /></div>
    <div className="stats-grid"><Stat title="Indexed failed events" value={summary.total} icon={Inbox} detail="Across 1 connected dead-letter queue" /><Stat title="Eligible for replay" value={eligible} icon={ShieldCheck} detail="Confirmed NOT STARTED by business adapter" tone="accent" /><Stat title="Needs manual review" value={review} icon={TriangleAlert} detail="Unknown or in-progress business outcomes" /><Stat title="Verified recoveries" value={summary.verified} icon={CheckCheck} detail="Business outcome confirmed after replay" /></div>
    <div className="overview-columns"><section className="panel failures-panel"><div className="panel-heading"><div><h2>Failure groups<span className="count-pill">{summary.groups.length}</span></h2><p>Related events, grouped by error signature</p></div><button className="text-button" onClick={() => openInbox('')}>View inbox<ArrowRight size={15} /></button></div><GroupTable groups={summary.groups} openInbox={openInbox} inspect={inspect} compact /><div className="panel-note"><LockKeyhole size={14} />Inspection uses snapshots. Original queue messages stay untouched.</div></section>
      <section className="panel safety-panel"><div className="panel-heading"><div><h2>Replay safeguards</h2><p>Checks that come before every send</p></div><ShieldCheck size={21} className="green-text" /></div><div className="safeguard-list">{[['Business idempotency', 'Stable operation ID and consumer contract'], ['Outcome verification', 'Unknown outcomes block automatic replay'], ['Human approval', 'Versioned plan and exact hash match'], ['Controlled destination', 'Isolated queue, bounded replay rate']].map(([name, text]) => <div key={name}><span className="check-circle"><Check size={13} /></span><div><strong>{name}</strong><small>{text}</small></div></div>)}</div><div className="safety-footer"><span className="live-dot" />All safeguards active<span>4 / 4</span></div></section></div>
    <div className="overview-columns lower"><section className="panel"><div className="panel-heading"><div><h2>The recovery workflow</h2><p>Deliberate steps. Traceable outcomes.</p></div><GitBranch size={19} className="muted" /></div><div className="workflow">{[[Search, 'Investigate', 'Understand the failure', 'inbox'], [FileCheck2, 'Plan & approve', 'Review eligible events', 'plans'], [Radio, 'Replay & verify', 'Confirm business recovery', 'jobs']].map(([Icon, title, subtitle, target], i) => <React.Fragment key={title}>{i > 0 && <ChevronRight className="workflow-arrow" size={17} />}<button onClick={() => navigate(target)}><span><Icon size={20} /><em>0{i + 1}</em></span><strong>{title}</strong><small>{subtitle}</small></button></React.Fragment>)}</div></section><section className="panel"><div className="panel-heading"><div><h2>Recent activity</h2><p>A durable record of recovery decisions</p></div><button className="text-button" onClick={() => navigate('audit')}>View audit<ArrowRight size={15} /></button></div><div className="recent-activity">{activity.length ? activity.map(a => <div key={a.sequence}><span className="activity-icon"><FileText size={15} /></span><div><strong>{a.action.toLowerCase().replaceAll('_', ' ')}</strong><small>{a.actor} · {age(a.at)} ago</small></div><span className="activity-dot" /></div>) : <p className="muted">Recovery actions will appear here.</p>}</div></section></div>
  </>;
}

function GroupTable({ groups, openInbox, inspect, compact }) {
  return <div className="table-scroll"><table className="group-table"><thead><tr><th>Error signature</th><th>Events</th><th>Oldest</th><th>Disposition</th><th /></tr></thead><tbody>{groups.map((g, i) => <tr key={g.id}><td><button className="error-name" onClick={() => openInbox(g.error)}><span className={'error-icon ' + (i === 0 ? 'amber' : i === 1 ? 'red' : 'gray')}><Terminal size={15} /></span><span><strong>{g.error}</strong>{!compact && <small>shipment-worker · {g.ELIGIBLE} eligible</small>}</span></button></td><td className="numeric">{fmt(g.count)}</td><td className="muted">{age(g.oldest)}</td><td><Badge color={g.BLOCKED ? 'red' : g.REVIEW ? 'amber' : 'gray'}>{g.BLOCKED ? 'Blocked' : g.REVIEW ? 'Review' : 'Skip'}</Badge></td><td><button className="icon-button" aria-label={'Inspect ' + g.error} onClick={() => inspect(g.sampleId)}><ArrowUpRight size={17} /></button></td></tr>)}</tbody></table>{!groups.length && <Empty title="No matching failure groups" text="Try a different error or search term." />}</div>;
}

function InboxPage({ initialFilter, investigation, summary, inspect, navigate, canOperate, notify }) {
  const [error, setError] = useState(initialFilter || '');
  const [search, setSearch] = useState('');
  const [hours, setHours] = useState('24');
  const [outcome, setOutcome] = useState('');
  const [cursor, setCursor] = useState(0);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { setCursor(0); }, [error, search, hours, outcome]);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      setLoading(true);
      api('/messages' + query({ error, search, hours, outcome, cursor })).then(r => { if (active) setResults(r); }).catch(e => notify(e.message, true)).finally(() => { if (active) setLoading(false); });
    }, 180);
    return () => { active = false; clearTimeout(timer); };
  }, [error, search, hours, outcome, cursor]);
  return <><SectionTitle eyebrow="DISCOVER & INVESTIGATE" title={investigation ? 'Follow the evidence.' : 'Failure inbox'} text="Explore indexed snapshots without receiving or deleting live queue messages."><button className="primary" disabled={!canOperate} onClick={() => { sessionStorage.setItem('qr-plan-filters', JSON.stringify({ error, search, hours, outcome })); navigate('plans'); }}><Plus size={16} />Create replay plan</button></SectionTitle>
    <div className="queue-summary"><div className="queue-summary-icon"><Boxes size={22} /></div><div><strong>shipment-worker-dlq</strong><small>Standard queue · us-east-1 · shipment-worker</small></div><Badge color="green">Snapshots available</Badge><span>{fmt(summary.total)} indexed events</span></div>
    <div className="filter-bar"><label className="search-field"><Search size={17} /><input aria-label="Search operation or message ID" placeholder="Search operation or message ID…" value={search} onChange={e => setSearch(e.target.value)} /></label><select aria-label="Time range" value={hours} onChange={e => setHours(e.target.value)}><option value="24">Last 24 hours</option><option value="8">Last 8 hours</option><option value="2">Last 2 hours</option><option value="">All indexed events</option></select><select aria-label="Error type" value={error} onChange={e => setError(e.target.value)}><option value="">All error types</option>{summary.groups.map(g => <option key={g.error}>{g.error}</option>)}</select><select aria-label="Business outcome" value={outcome} onChange={e => setOutcome(e.target.value)}><option value="">All business outcomes</option>{['NOT_STARTED', 'COMPLETE', 'UNKNOWN', 'IN_PROGRESS'].map(x => <option key={x}>{x}</option>)}</select></div>
    <section className="panel"><div className="panel-heading"><h2>Failed events<span className="count-pill">{fmt(results?.total)}</span></h2><span className="subtle-label">{loading ? 'Updating…' : 'Payloads masked · SHA-256 snapshots'}</span></div><div className="table-scroll"><table><thead><tr><th>Message / operation</th><th>Error</th><th>Business outcome</th><th>Replay decision</th><th>Age</th><th /></tr></thead><tbody>{results?.items.map(m => <tr key={m.id}><td><button className="row-link" onClick={() => inspect(m.id)}><strong className="mono">{m.operationId}</strong><small className="mono">{m.id}</small></button></td><td>{m.error}</td><td><Badge>{m.outcome}</Badge></td><td><Badge>{m.decision}</Badge></td><td className="muted">{age(m.observedAt)}</td><td><button className="icon-button" aria-label={'Inspect ' + m.id} onClick={() => inspect(m.id)}><ArrowUpRight size={17} /></button></td></tr>)}</tbody></table></div>{results && !results.items.length && <Empty title="No matching events" text="Adjust the filters to find other snapshots." />}<PageButtons cursor={cursor} next={results?.nextCursor} setCursor={setCursor} total={results?.total || 0} /></section>
    <div className="inline-note"><ShieldCheck size={17} />An eligible result is evidence for a plan. The worker checks it again immediately before sending.</div>
  </>;
}

function Modal({ title, close, children, wide = false }) {
  useEffect(() => { const fn = e => { if (e.key === 'Escape') close(); }; addEventListener('keydown', fn); const old = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { removeEventListener('keydown', fn); document.body.style.overflow = old; }; }, []);
  return <div className="modal-backdrop" onClick={close}><section className={'modal ' + (wide ? 'wide' : '')} role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}><div className="modal-heading"><h2>{title}</h2><button autoFocus className="icon-button" aria-label="Close dialog" onClick={close}><X size={20} /></button></div>{children}</section></div>;
}

function MessageDrawer({ message: m, close, create }) {
  return <Modal title="Event investigation" close={close} wide><div className="drawer-body"><div className="detail-title"><span className="eyebrow">ORIGINAL EVENT SNAPSHOT</span><h2 className="mono">{m.operation_id}</h2><Badge>{m.decision}</Badge><p>{m.reason}</p></div><div className="detail-facts"><div><span>Message ID</span><strong className="mono">{m.id}</strong></div><div><span>Error signature</span><strong>{m.error}</strong></div><div><span>Business outcome</span><strong>{m.outcome}</strong></div><div><span>Original DLQ message</span><strong className="green-text">Retained · never deleted</strong></div></div><h3>Payload <span className="subtle-label">Sensitive fields masked</span></h3><pre>{JSON.stringify(m.payload, null, 2)}</pre><h3>Correlated evidence <Badge color="gray">Simulated</Badge></h3><div className="evidence-list">{m.evidence.map(e => <div key={e.kind}><span className="evidence-dot" /><div><strong>{e.kind}</strong><p>{e.detail}</p><small>{date(e.at)}</small></div></div>)}</div><h3>Snapshot integrity</h3><div className="hash-display"><Fingerprint size={16} /><code>{m.checksum}</code></div><h3>Preserved message attributes</h3><pre>{JSON.stringify(m.attributes, null, 2)}</pre><div className="modal-actions"><button className="secondary" onClick={close}>Close investigation</button><button className="primary" onClick={create}>Open replay plans<ArrowRight size={16} /></button></div></div></Modal>;
}

function Plans({ actor, version, act, busy, notify, canOperate, canApprove, navigate }) {
  const [plans, setPlans] = useState([]);
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState(() => { try { return JSON.parse(sessionStorage.getItem('qr-plan-filters') || '{}'); } catch { return {}; } });
  const [rate, setRate] = useState(25);
  const [note, setNote] = useState('Reviewed business outcomes, deployment, and replay limits.');
  const [fault, setFault] = useState(false);
  useEffect(() => { api('/replay-plans').then(r => { setPlans(r.items); setSelected(s => r.items.find(p => p.id === s?.id) || r.items[0] || null); }).catch(e => notify(e.message, true)); }, [version, actor]);
  const create = async e => { e.preventDefault(); const p = await act(() => api('/replay-plans', { filters: filter, rate: Number(rate), destination: 'shipment-replay-queue' }), 'Dry run created. Review the exclusions before requesting approval.'); if (p) { setSelected(p); setCreating(false); } };
  return <><SectionTitle eyebrow="PLAN BEFORE YOU SEND" title="Replay plans" text="An immutable selection, explicit exclusions, and a human decision."><button className="primary" disabled={!canOperate} onClick={() => setCreating(true)}><Plus size={16} />New dry run</button></SectionTitle>
    {!plans.length ? <section className="panel"><Empty icon={FileCheck2} title="Every recovery starts with a plan." text="Create a dry run to check business outcomes, preview eligible events, and isolate anything uncertain." action={<button className="primary" disabled={!canOperate} onClick={() => setCreating(true)}><Plus size={16} />Create your first plan</button>} /></section> : <div className="split-layout"><section className="panel plan-list"><div className="panel-heading"><h2>Saved plans <span className="count-pill">{plans.length}</span></h2></div>{plans.map(p => <button key={p.id} className={'plan-list-item ' + (p.id === selected?.id ? 'selected' : '')} onClick={() => setSelected(p)}><div><span className="mono">{p.id}</span><Badge>{p.expired && p.state === 'READY' ? 'EXPIRED' : p.state}</Badge></div><strong>{fmt(p.counts.ELIGIBLE)} eligible <span>/ {fmt(p.decisions.length)} selected</span></strong><small>{p.filters.error || 'All error groups'} · {p.rate} msg/sec</small></button>)}</section>
      {selected && <section className="panel plan-detail"><div className="panel-heading"><div><h2>Recovery preview <Badge>{selected.state}</Badge></h2><p className="mono">{selected.id} · version {selected.version}</p></div><button className="icon-button" aria-label="Export dry run" onClick={() => download(selected, selected.id + '.json')}><ArrowDownToLine size={18} /></button></div><div className="plan-content"><div className="plan-counters">{[['ELIGIBLE', 'Candidates'], ['SKIP', 'Already complete / duplicate'], ['REVIEW', 'Needs review'], ['BLOCKED', 'Blocked']].map(([k, text]) => <div key={k}><strong className={stateColor(k) + '-text'}>{fmt(selected.counts[k])}</strong><span>{text}</span></div>)}</div><div className="info-grid"><div><span>Controlled destination</span><strong className="mono">{selected.destination}</strong></div><div><span>Maximum send rate</span><strong>{selected.rate} messages / second</strong></div><div><span>Expected side effect</span><strong>{selected.expectedSideEffect}</strong></div><div><span>Approval expires</span><strong>{date(selected.expiresAt)}</strong></div></div><div className="inline-note"><ShieldCheck size={17} />Only eligible snapshots are sent. Original DLQ messages are retained.</div><h3>Versioned plan hash</h3><div className="hash-display"><Fingerprint size={16} /><code>{selected.hash}</code></div><h3>Decision sample <span className="subtle-label">First 8 of {fmt(selected.decisions.length)} · full details in export</span></h3><div className="decision-list">{selected.decisions.slice(0, 8).map(d => <div key={d.messageId}><span className="mono">{d.operationId}</span><Badge>{d.decision}</Badge></div>)}</div>
        {selected.state === 'READY' && <div className="approval-block"><h3><LockKeyhole size={17} />Human approval required</h3><p>Created by {selected.created_by}. A different person with Approver permissions must approve this exact plan.</p>{canApprove && <label>Approval note<textarea value={note} onChange={e => setNote(e.target.value)} /></label>}<button className="primary" disabled={busy || !canApprove || selected.expired || selected.created_by === actor.name} onClick={() => act(() => api('/replay-plans/' + selected.id + '/approve', { planHash: selected.hash, note }), 'Exact plan approved. Switch to Operator to execute.')}><ShieldCheck size={16} />Approve exact plan</button>{!canApprove && <small>Use the demo identity menu to switch to Approver.</small>}</div>}
        {selected.state === 'APPROVED' && <div className="approval-block"><h3><CheckCheck size={17} />Approved by {selected.approved_by}</h3><p>{selected.approval_note}</p><label className="checkbox-label"><input type="checkbox" checked={fault} onChange={e => setFault(e.target.checked)} />Simulate interruption after first send (reconciliation demo)</label><button className="primary" disabled={busy || !canOperate || selected.expired} onClick={async () => { const j = await act(() => api('/replay-jobs', { approvedPlanId: selected.id, simulateCrash: fault }), 'Recovery started with the approved rate and selection.'); if (j) navigate('jobs'); }}><Play size={16} />Start approved recovery</button>{!canOperate && <small>Switch to Operator to start this approved plan.</small>}</div>}
        {selected.state === 'EXECUTING' && <button className="primary" onClick={() => navigate('jobs')}>Open recovery console<ArrowRight size={16} /></button>}
      </div></section>}</div>}
    {creating && <Modal title="Create a replay dry run" close={() => setCreating(false)}><form onSubmit={create} className="form-body"><p>Check snapshots against the business status adapter. This step sends no messages.</p><label>Error group<select value={filter.error || ''} onChange={e => setFilter({ ...filter, error: e.target.value })}><option value="">All indexed error groups</option><option>WarehouseTimeout</option><option>InvalidSchema</option><option>AlreadyShipped</option></select></label><label>Operation or message ID (optional)<input placeholder="e.g. shipment:ORD-12345" value={filter.search || ''} onChange={e => setFilter({ ...filter, search: e.target.value })} /></label><label>Destination<input readOnly value="shipment-replay-queue" /></label><label>Maximum replay rate (messages / second)<input type="number" min="1" max="100" required value={rate} onChange={e => setRate(e.target.value)} /></label><div className="inline-note"><LockKeyhole size={17} />Requires a separate approver. Valid for one hour.</div><div className="modal-actions"><button type="button" className="secondary" onClick={() => setCreating(false)}>Cancel</button><button disabled={busy} className="primary"><FileCheck2 size={16} />Generate dry run</button></div></form></Modal>}
  </>;
}

function Jobs({ actor, version, act, busy, notify, canOperate }) {
  const [jobs, setJobs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  useEffect(() => { let active = true; const load = () => api('/replay-jobs').then(r => { if (active) setJobs(r.items); }).catch(e => { if (active) notify(e.message, true); }); load(); const t = setInterval(load, 1200); return () => { active = false; clearInterval(t); }; }, [version, actor]);
  const job = jobs.find(j => j.id === selectedId) || jobs[0];
  const control = action => act(() => api('/replay-jobs/' + job.id + '/' + action, { reason: 'Operator requested ' + action }), 'Job ' + action + ' recorded.');
  const verify = () => act(() => api('/verifications', { jobId: job.id, adapter: 'shipment-status' }), 'Business outcomes checked. Uncertain deliveries were not resent.');
  return <><SectionTitle eyebrow="CONTROLLED EXECUTION" title="Recovery console" text="Watch each attempt move from queued to a verified business outcome."><span className="subtle-label"><span className="live-dot" />Live · updates every 1.2s</span></SectionTitle>
    {!job ? <section className="panel"><Empty icon={Activity} title="No recovery jobs yet" text="Approve a replay plan, then start it as Operator. Execution and verification will appear here." /></section> : <>
      <div className="job-selector"><label>Recovery job<select value={job.id} onChange={e => setSelectedId(e.target.value)}>{jobs.map(j => <option key={j.id} value={j.id}>{j.id} · {j.state}</option>)}</select></label><button className="secondary" onClick={async () => { const r = await act(() => api('/replay-jobs/' + job.id + '/report')); if (r) download(r, job.id + '-report.json'); }}><ArrowDownToLine size={16} />Incident report</button></div>
      <section className="panel job-header"><div><div className="eyebrow">{job.plan.destination} · {job.plan.rate} MSG/SEC MAX</div><h2>{job.id}<Badge>{job.state}</Badge></h2><p>Approved by {job.plan.approved_by} · started {date(job.created_at)}</p></div><div className="job-controls">{job.state === 'RUNNING' && <button disabled={!canOperate || busy} className="secondary" onClick={() => control('pause')}><Pause size={16} />Pause</button>}{job.state === 'PAUSED' && <button disabled={!canOperate || busy || job.counts.UNCERTAIN > 0} className="primary" onClick={() => control('resume')}><Play size={16} />Resume</button>}{!['COMPLETED', 'CANCELLED', 'PARTIAL', 'FAILED'].includes(job.state) && <button disabled={!canOperate || busy} className="danger-button" onClick={() => control('cancel')}><Square size={14} />Cancel</button>}<button disabled={!canOperate || busy} className="primary" onClick={verify}><CheckCheck size={16} />{job.counts.UNCERTAIN ? 'Reconcile outcomes' : 'Verify outcomes'}</button></div></section>
      {job.reason && <div className="inline-note amber-note"><TriangleAlert size={18} />{job.reason}</div>}
      <div className="job-stats">{[['QUEUED', Clock3], ['SENT', Radio], ['VERIFIED', CheckCheck], ['UNCERTAIN', TriangleAlert], ['REVIEW', Search], ['CANCELLED', Square]].map(([key, Icon]) => <div key={key}><Icon size={17} /><strong>{fmt(job.counts[key])}</strong><span>{key.toLowerCase()}</span></div>)}</div>
      <section className="panel progress-panel"><div><h3>Business verification coverage</h3><strong>{Math.round(job.counts.VERIFIED / Math.max(1, job.total) * 100)}%</strong></div><div className="progress-track"><span style={{ width: `${job.counts.VERIFIED / Math.max(1, job.total) * 100}%` }} /></div><p>{fmt(job.counts.VERIFIED)} of {fmt(job.total)} approved candidates verified. Sending alone is never counted as recovery.</p></section>
      <section className="panel"><div className="panel-heading"><div><h2>Replay ledger</h2><p>Durable attempt intent, delivery state, and outcome evidence</p></div><span className="subtle-label">First 100 attempts</span></div><div className="table-scroll"><table><thead><tr><th>Business operation</th><th>Attempt ID</th><th>State</th><th>Latest evidence</th></tr></thead><tbody>{job.attempts.map(a => <tr key={a.id}><td className="mono">{a.operation_id}</td><td className="mono muted">{a.id}</td><td><Badge>{a.state}</Badge></td><td className="evidence-cell">{a.detail || 'Waiting for rate-limited scheduling'}</td></tr>)}</tbody></table></div></section><div className="inline-note"><LockKeyhole size={17} />Original DLQ messages remain intact. This prototype does not expose source-message deletion.</div>
    </>}
  </>;
}

function Audit({ version, notify }) {
  const [data, setData] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [search, setSearch] = useState('');
  useEffect(() => { let active = true; api('/audit' + query({ cursor, search })).then(r => { if (active) setData(r); }).catch(e => notify(e.message, true)); return () => { active = false; }; }, [cursor, search, version]);
  return <><SectionTitle eyebrow="ACCOUNTABILITY BY DEFAULT" title="Audit trail" text="Append-only records of plans, approvals, execution, and reconciliation."><button className="secondary" onClick={() => download(data, 'queuerescue-audit-page.json')} disabled={!data}><ArrowDownToLine size={16} />Export this page</button></SectionTitle><div className="filter-bar"><label className="search-field"><Search size={17} /><input aria-label="Search audit records" value={search} onChange={e => { setSearch(e.target.value); setCursor(0); }} placeholder="Search actor, action, or resource…" /></label><Badge color={data?.integrity === 'VALID' ? 'green' : 'red'}>{data?.integrity === 'VALID' ? 'Hash chain verified' : 'Checking integrity'}</Badge></div><section className="panel"><div className="table-scroll"><table><thead><tr><th>Time / actor</th><th>Action</th><th>Resource</th><th>Evidence</th></tr></thead><tbody>{data?.items.map(a => <tr key={a.sequence}><td><strong>{date(a.at)}</strong><small>{a.actor} · {a.role}</small></td><td><span className="audit-action">{a.action.replaceAll('_', ' ')}</span></td><td className="mono">{a.resource}</td><td><details><summary>Decision details</summary><pre>{JSON.stringify(JSON.parse(a.detail), null, 2)}</pre><small className="mono break-word">Record hash: {a.hash}</small></details></td></tr>)}</tbody></table></div>{data && !data.items.length && <Empty title="No matching audit records" text="Try a different search." />}<PageButtons cursor={cursor} next={data?.nextCursor} total={data?.total || 0} setCursor={setCursor} /></section><div className="inline-note"><Database size={17} />Local append-only triggers and a hash chain detect accidental edits. Production requires a separately protected audit sink.</div></>;
}

function Connections({ notify }) {
  const [connection, setConnection] = useState(null);
  useEffect(() => { api('/connections').then(r => setConnection(r.items[0])).catch(e => notify(e.message, true)); }, []);
  return <><SectionTitle eyebrow="EXPLICITLY ALLOWLISTED" title="Connections" text="One standard queue, one controlled destination, and an application-owned verifier." /><section className="panel connection-panel"><div className="panel-heading"><div><h2><Boxes size={22} />Shipment recovery</h2><p>Single-account, single-region prototype</p></div><Badge color="blue">Simulated connection</Badge></div>{connection && <div className="connection-body"><div className="connection-flow">{[[Boxes, connection.source, 'Source queue'], [Terminal, connection.worker, 'Lambda consumer'], [Inbox, connection.id, 'Dead-letter queue']].map(([Icon, value, label], i) => <React.Fragment key={label}>{i > 0 && <ArrowRight size={20} className="muted" />}<div><Icon size={25} /><strong className="mono">{value}</strong><small>{label}</small></div></React.Fragment>)}</div><div className="info-grid">{[['Replay destination', connection.destination], ['Queue type', connection.type], ['Region', connection.region], ['Consumer deployment', connection.consumerVersion], ['Status adapter', connection.verifier], ['Idempotency contract', connection.idempotency], ['Log group', connection.logGroup], ['Evidence retention', 'Local snapshots persist until the demo database is removed']].map(([k, v]) => <div key={k}><span>{k}</span><strong>{v}</strong></div>)}</div><div className="inline-note"><ShieldCheck size={18} />AWS adapters, Cognito, KMS, and Terraform deployment belong to the next implementation phase. No cloud access keys are used here.</div></div>}</section></>;
}

function Bootstrap() {
  const [workspace] = useState(() => {
    try { return { config: dashboardConfig(import.meta.env, location) }; }
    catch (error) { return { error: error.message }; }
  });
  if (workspace.error) return <div className="cloud-login"><section className="panel cloud-login-card"><h1>AWS workspace isn’t configured.</h1><p role="alert">{workspace.error}</p><p>Ask your workspace administrator to finish the connection settings.</p></section></div>;
  return workspace.config.mode === 'aws' ? <CloudApp config={workspace.config} /> : <App />;
}

createRoot(document.getElementById('root')).render(<Bootstrap />);
