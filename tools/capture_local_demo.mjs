// Optional real-browser capture. Publishes nothing; all raw output stays ignored.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, mkdtemp, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { chromium } = require(join(root, '.data/showcase-tools/node_modules/playwright'));
const sha = value => createHash('sha256').update(value).digest('hex');
async function protectedFiles() {
  const result = {};
  async function visit(directory) {
    let entries;
    try { entries = await readdir(directory, { withFileTypes: true }); }
    catch (e) { if (e.code === 'ENOENT') return; throw e; }
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile()) result[path] = sha(await readFile(path));
    }
  }
  await visit(join(root, 'dist'));
  for (const name of ['.env.local', '.env']) {
    try { result[name] = sha(await readFile(join(root, name))); }
    catch (e) { if (e.code !== 'ENOENT') throw e; }
  }
  return result;
}

const before = await protectedFiles();
await mkdir(join(root, '.data'), { recursive: true });
const directory = await mkdtemp(join(root, '.data/showcase-'));
const media = join(directory, 'media');
await mkdir(media);
console.log('Capture workspace:', directory);
let server, browser, context, page;
let interrupted = false, captureComplete = false;
const interrupt = () => {
  interrupted = true;
  if (browser) void browser.close().catch(() => {});
  if (server && server.exitCode === null) server.kill('SIGINT');
};
process.once('SIGINT', interrupt);
process.once('SIGTERM', interrupt);
const chapters = [], errors = [], blocked = [];
try {
  // Disable dotenv, public-directory copying and local Vite plugins. Only this
  // child process's environment changes; the running app/settings stay intact.
  for (const name of Object.keys(process.env)) if (/^(VITE_|AWS_|QR_)/.test(name)) delete process.env[name];
  process.env.VITE_QR_MODE = 'local';
  const { build } = await import('vite');
  await build({ root, configFile: false, envDir: false, publicDir: false,
    build: { outDir: join(directory, 'dist'), emptyOutDir: true, sourcemap: false } });
  await writeFile(join(directory, 'dist/connect-origins.json'), '[]');
  assert(!interrupted, 'Capture cancelled.');
  server = spawn(join(root, '.venv/bin/python'), ['-u', '-m', 'tools.serve_local_showcase', directory], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  server.stderr.on('data', b => { stderr += b; });
  const port = await new Promise((accept, reject) => {
    let buffer = '';
    const timeout = setTimeout(() => reject(Error('Isolated server did not start.')), 15000);
    server.stdout.on('data', b => {
      buffer += b;
      if (buffer.includes('\n')) { clearTimeout(timeout); accept(JSON.parse(buffer.split('\n')[0]).port); }
    });
    server.once('exit', () => { clearTimeout(timeout); reject(Error('Isolated server exited before capture.')); });
  });
  const origin = `http://127.0.0.1:${port}`;
  browser = await chromium.launch({ headless: true, handleSIGINT: false, handleSIGTERM: false });
  assert(!interrupted, 'Capture cancelled.');
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1,
    locale: 'en-US', timezoneId: 'America/New_York', reducedMotion: 'reduce',
    recordVideo: { dir: join(directory, 'recordings'), size: { width: 1440, height: 1000 } } });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === origin) return route.continue();
    blocked.push(url.origin);
    return route.abort();
  });
  page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  const began = Date.now();
  const pause = ms => page.waitForTimeout(ms); // Intentional viewing time, never a readiness check.
  const ready = async () => { await page.evaluate(() => document.fonts.ready); await page.locator('.simulation-tag').filter({ hasText: 'LOCAL SIMULATION' }).waitFor(); };
  const privacy = async () => {
    const text = await page.locator('body').innerText();
    assert(!text.includes('customer@example.com') && !text.includes('42 Example Street'), 'Unmasked synthetic contact fields in capture.');
  };
  const chapter = async title => { await ready(); await privacy(); chapters.push({ title, seconds: Math.round((Date.now() - began) / 1000) }); console.log('Scene:', title); };
  const shot = async filename => {
    await privacy();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForFunction(() => window.scrollY === 0);
    await page.screenshot({ path: join(media, filename), fullPage: ['03-dry-run-plan.png', '04-separate-approval.png'].includes(filename) });
  };
  const dismiss = async () => { const button = page.getByRole('button', { name: 'Dismiss notification', exact: true }); if (await button.count()) await button.click(); };
  await page.goto(origin);
  await page.getByRole('heading', { name: 'A clear path from failure to recovery.', exact: true }).waitFor();
  await chapter('Local simulation overview'); await pause(4500);
  await page.getByRole('button', { name: 'Open failure inbox', exact: true }).click();
  await page.locator('.panel-heading .count-pill').filter({ hasText: /^1,000$/ }).waitFor();
  await chapter('Investigate retained failures'); await shot('01-failure-inbox.png'); await pause(6500);
  await page.getByRole('textbox', { name: 'Search operation or message ID' }).fill('msg-000346');
  await page.getByRole('button', { name: 'Inspect msg-000346', exact: true }).click();
  await page.getByRole('dialog', { name: 'Event investigation' }).waitFor();
  await page.getByText('[REDACTED]', { exact: false }).first().waitFor();
  await chapter('Inspect masked payload and simulated evidence'); await shot('02-masked-inspection.png'); await pause(8500);
  await page.getByRole('button', { name: 'Close investigation', exact: true }).click();
  await page.getByRole('button', { name: 'Replay plans', exact: true }).click();
  await page.getByRole('button', { name: 'New dry run', exact: true }).click();
  await page.getByRole('button', { name: 'Generate dry run', exact: true }).click();
  await page.getByRole('heading', { name: /^Recovery preview/ }).waitFor();
  await dismiss();
  assert.match(await page.locator('.plan-detail').innerText(), /750/);
  await chapter('Preview eligibility and exclusions without sending'); await shot('03-dry-run-plan.png'); await pause(9000);
  await page.getByRole('button', { name: 'New dry run', exact: true }).click();
  await page.getByPlaceholder('e.g. shipment:ORD-12345').fill('shipment:ORD-1234');
  await page.getByRole('spinbutton', { name: 'Maximum replay rate (messages / second)' }).fill('2');
  await page.getByRole('button', { name: 'Generate dry run', exact: true }).click();
  await page.locator('.plan-list-item.selected').filter({ hasText: '10 eligible' }).waitFor();
  await page.getByRole('combobox', { name: 'Demo identity', exact: true }).selectOption('Approver');
  await page.getByRole('button', { name: 'Approve exact plan', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('textbox', { name: 'Approval note', exact: true }).fill('Reviewed 10 synthetic candidates, exact plan hash and isolated destination.');
  await chapter('A separate simulated approver reviews the exact plan'); await pause(6500);
  await page.getByRole('button', { name: 'Approve exact plan', exact: true }).click();
  await page.getByRole('heading', { name: 'Approved by Morgan Lee', exact: true }).waitFor();
  await page.getByRole('combobox', { name: 'Demo identity', exact: true }).selectOption('Operator');
  await page.getByRole('button', { name: 'Start approved recovery', exact: true }).click({ trial: true });
  await dismiss();
  await chapter('Operator starts the independently approved selection'); await shot('04-separate-approval.png'); await pause(6500);
  await page.getByRole('checkbox', { name: 'Simulate interruption after first send (reconciliation demo)' }).check();
  await page.getByRole('button', { name: 'Start approved recovery', exact: true }).click();
  await page.getByRole('heading', { name: 'Recovery console', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Reconcile outcomes', exact: true }).waitFor();
  await dismiss();
  await chapter('Interruption leaves an uncertain delivery'); await shot('06-interruption-reconciliation.png'); await pause(7500);
  await page.getByRole('button', { name: 'Reconcile outcomes', exact: true }).click();
  await page.getByRole('button', { name: 'Resume', exact: true }).click({ trial: true });
  await chapter('Reconcile business evidence before resuming'); await pause(5500);
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await page.locator('.job-header .badge').filter({ hasText: 'VERIFYING' }).waitFor({ timeout: 30000 });
  await chapter('Remaining candidates replay at the approved rate'); await pause(4500);
  await page.getByRole('button', { name: 'Verify outcomes', exact: true }).click();
  await page.locator('.job-header .badge').filter({ hasText: 'COMPLETED' }).waitFor({ timeout: 15000 });
  await page.locator('.progress-panel strong').filter({ hasText: /^100%$/ }).waitFor();
  await dismiss();
  await chapter('Verify actual simulated business completion'); await shot('05-verified-recovery.png'); await pause(8500);
  await page.getByRole('button', { name: 'Audit trail', exact: true }).click();
  await page.getByRole('heading', { name: 'Audit trail', exact: true }).waitFor();
  await page.getByText('Hash chain verified', { exact: true }).waitFor();
  await chapter('Review the durable local audit trail'); await pause(5000);
  assert.deepEqual(blocked, [], 'Capture attempted an external connection.');
  assert.deepEqual(errors, [], 'Browser errors occurred.');
  const video = page.video();
  await context.close(); context = null;
  await video.saveAs(join(media, 'queuerescue-local-demo.webm'));
  assert.deepEqual(await protectedFiles(), before, 'Existing dist or local env files changed.');
  await writeFile(join(directory, 'capture.json'), JSON.stringify({ mode: 'Local simulation; synthetic data; no AWS', chapters, viewport: { width: 1440, height: 1000 }, blockedExternalRequests: blocked.length, browserErrors: errors, protectedFilesUnchanged: true }, null, 2));
  captureComplete = true;
  console.log('Media captured:', media);
} catch (error) {
  if (!interrupted && page && !page.isClosed()) await page.screenshot({ path: join(directory, 'failed.png'), fullPage: true });
  throw error;
} finally {
  if (context) await context.close().catch(() => {});
  if (browser) await browser.close().catch(() => {});
  if (server && server.exitCode === null) {
    const exited = once(server, 'exit');
    server.kill('SIGINT');
    await exited;
  }
  assert.deepEqual(await protectedFiles(), before, 'Existing dist or local env files changed.');
  await writeFile(join(root, '.data/showcase-latest.json'), JSON.stringify({ directory, media, captureComplete }, null, 2));
  process.removeListener('SIGINT', interrupt);
  process.removeListener('SIGTERM', interrupt);
}
