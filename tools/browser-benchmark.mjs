// Optional fixture around the real built app. No private React state or tokens.
const output = document.querySelector('#results');
const status = document.querySelector('#status');
const run = document.querySelector('#run');
const trials = [], longTasks = [];
const round = n => Math.round(n * 100) / 100;
const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
const doc = () => document;
const text = selector => doc()?.querySelector(selector)?.textContent || '';
const button = label => [...doc().querySelectorAll('button')].find(item => item.textContent.trim() === label || item.getAttribute('aria-label') === label);
const click = label => { const target = button(label); if (!target || target.disabled) throw Error('Unavailable button: ' + label); target.click(); };
const wait = async predicate => {
  const deadline = performance.now() + 15000;
  while (!predicate()) { if (performance.now() > deadline) throw Error('Timed out waiting for the expected DOM result'); await nextFrame(); }
  await nextFrame(); await nextFrame();
};
const measure = async (trial, label, action, ready) => {
  status.textContent = `Cycle ${trials.length + 1}: ${label}`;
  const start = performance.now(); action();
  const route = label === 'inspectMs' ? '/api/messages/msg-000011' : ['openPlansMs', 'generateDryRunMs'].includes(label) ? '/api/replay-plans' : '/api/messages?';
  await wait(() => performance.getEntriesByType('resource').some(entry => entry.startTime >= start && entry.responseEnd > 0 && (new URL(entry.name).pathname + new URL(entry.name).search).startsWith(route)) && ready());
  trial[label] = round(performance.now() - start);
};
const input = value => {
  const field = doc().querySelector('[aria-label="Search operation or message ID"]');
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(field, value);
  field.dispatchEvent(new window.Event('input', { bubbles: true }));
};
const inboxReady = total => text('.panel-heading .count-pill') === total && text('.subtle-label').includes('Payloads masked') && doc().querySelectorAll('main tbody tr').length === Math.min(Number(total.replaceAll(',', '')), 25);
const stats = values => {
  const sorted = [...values].sort((a, b) => a - b), n = sorted.length;
  return { samples: n, min: sorted[0], median: sorted[Math.floor(n / 2)], p95: sorted[Math.ceil(.95 * n) - 1], max: sorted[n - 1] };
};
const initialReady = wait(() => text('.simulation-tag').includes('LOCAL SIMULATION') && text('nav em') === '10,000' && text('main h1').length > 0).then(() => round(performance.now()));
// Avoid an unhandled rejection when opened against the wrong seed/build.
initialReady.catch(() => {});
run.addEventListener('click', async () => {
  run.disabled = true;
  let observer;
  try {
    if (!['localhost', '127.0.0.1'].includes(location.hostname)) throw Error('Benchmark requires a loopback host');
    const manifest = await fetch('/connect-origins.json').then(r => r.json());
    if (!Array.isArray(manifest) || manifest.length) throw Error('Benchmark requires a local-mode build');
    const initialReadyMs = await initialReady;
    click('Overview');
    await wait(() => location.hash === '#overview' && !!doc().querySelector('.overview-columns'));
    try {
      observer = new window.PerformanceObserver(list => longTasks.push(...list.getEntries().map(entry => ({ startMs: round(entry.startTime), durationMs: round(entry.duration) }))));
      observer.observe({ type: 'longtask', buffered: true });
    } catch { observer = null; /* Unsupported measurement is reported as null. */ }
    for (let cycle = 0; cycle < 5; cycle++) {
      const trial = {};
      await measure(trial, 'openInboxMs', () => doc().querySelectorAll('nav button')[1].click(), () => inboxReady('10,000'));
      await measure(trial, 'nextPageMs', () => click('Next page'), () => text('.pagination').includes('26–50') && text('.subtle-label').includes('Payloads masked'));
      await measure(trial, 'searchMs', () => input('msg-000011'), () => inboxReady('1') && text('main tbody').includes('msg-000011'));
      await measure(trial, 'inspectMs', () => click('Inspect msg-000011'), () => !!doc().querySelector('[role="dialog"][aria-label="Event investigation"]') && text('.drawer-body').includes('[REDACTED]'));
      click('Close investigation');
      await measure(trial, 'clearSearchMs', () => input(''), () => inboxReady('10,000'));
      await measure(trial, 'openPlansMs', () => click('Replay plans'), () => text('main h1') === 'Replay plans' && (cycle === 0 ? !!button('Create your first plan') : doc().querySelectorAll('.plan-list-item').length === cycle && text('.plan-detail').includes('Recovery preview')));
      click('New dry run');
      await wait(() => !!doc().querySelector('[role="dialog"][aria-label="Create a replay dry run"]'));
      await measure(trial, 'generateDryRunMs', () => click('Generate dry run'), () => !doc().querySelector('[role="dialog"]') && text('.plan-detail').includes('First 8 of 10,000') && doc().querySelectorAll('.plan-list-item').length === cycle + 1);
      trials.push(trial);
    }
    observer?.disconnect();
    const resources = window.performance.getEntriesByType('resource').filter(item => new URL(item.name).pathname.startsWith('/api/'));
    const results = { mode: 'local production React app + SQLite; scripted DOM actions; no AWS', measuredAt: new Date().toISOString(),
      browser: navigator.userAgent, viewport: { width: window.innerWidth, height: window.innerHeight }, messages: 10000, initialReadyMs,
      summary: Object.fromEntries(Object.keys(trials[0]).map(key => [key, stats(trials.map(trial => trial[key]))])), trials,
      longTasks: observer ? { count: longTasks.length, maxMs: longTasks.length ? Math.max(...longTasks.map(item => item.durationMs)) : 0, entries: longTasks } : null,
      apiResources: { count: resources.length, maxDurationMs: round(Math.max(...resources.map(item => item.duration))) },
      safety: 'Only five unapproved plans created; verify sends/jobs/originals separately in the local database' };
    output.textContent = JSON.stringify(results, null, 2);
    for (const [label, values] of Object.entries(results.summary)) {
      const row = document.createElement('tr');
      for (const value of [label, values.samples, values.median, values.p95]) { const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell); }
      document.querySelector('#summary tbody').append(row);
    }
    status.textContent = 'Complete. Copy the JSON to ignored .data/ for analysis; inspect database safety invariants.';
  } catch (error) { const stage = status.textContent; status.textContent = 'Stopped: ' + error.message; output.textContent = JSON.stringify({ error: error.message, stage, trials }, null, 2); }
  finally { observer?.disconnect(); }
});
