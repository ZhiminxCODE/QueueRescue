// Offline only: build a public AWS dashboard release without changing .env or dist.
import { readFile, writeFile, mkdir, mkdtemp, readdir, rename, rm, lstat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dashboardConfig } from '../src/cloud/config.js';

const ROOT = fileURLToPath(new URL('../', import.meta.url));

export function hostedEnvironment(deployment) {
  const site = deployment.dashboard_hosting;
  if (!site?.provisioned || !/^[a-z0-9]+\.cloudfront\.net$/.test(site.domain_name || '') ||
      site.callback_url !== `https://${site.domain_name}/callback` || !/^[A-Z0-9]{1,32}$/.test(site.distribution_id || '') ||
      !/^[0-9]{12}$/.test(deployment.account_id || '')) throw new Error('Use canonical provisioned dashboard hosting outputs.');
  const env = { VITE_QR_MODE: 'aws', VITE_QR_REGION: deployment.region, VITE_QR_API_URL: deployment.api_url,
    VITE_QR_COGNITO_URL: deployment.cognito_login_url, VITE_QR_COGNITO_CLIENT_ID: deployment.cognito_client_id,
    VITE_QR_REDIRECT_URI: site.callback_url };
  dashboardConfig(env, new URL(site.callback_url));
  if (!new RegExp(`^queuerescue-(dev|test)-${deployment.account_id}-${deployment.region}-dashboard$`).test(site.bucket || '')) throw new Error('Use this deployment’s dedicated dashboard bucket.');
  return env;
}

export function hostingCsp(env) {
  const config = dashboardConfig(env, new URL(env.VITE_QR_REDIRECT_URI));
  return `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ${config.apiUrl} ${config.loginUrl}; object-src 'none'; base-uri 'none'; form-action 'none'`;
}

export function releaseManifest(files, deployment) {
  const env = hostedEnvironment(deployment), policy = hostingCsp(env);
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  if (!files['index.html']?.toString().includes(meta)) throw new Error('Release HTML is missing its exact-origin CSP.');
  if (JSON.stringify(JSON.parse(files['connect-origins.json']?.toString() || 'null')) !== JSON.stringify([env.VITE_QR_API_URL, env.VITE_QR_COGNITO_URL])) throw new Error('Release connection policy does not match deployment.');
  const rows = Object.entries(files).sort(([a], [b]) => a.localeCompare(b)).map(([key, body]) => {
    const asset = /^assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{6,}\.(js|css)$/.test(key);
    if (!asset && !['index.html', 'connect-origins.json'].includes(key)) throw new Error('Unexpected release file: ' + key);
    return { key, bytes: body.length, sha256: createHash('sha256').update(body).digest('hex'),
      contentType: key.endsWith('.js') ? 'text/javascript' : key.endsWith('.css') ? 'text/css' : key.endsWith('.json') ? 'application/json' : 'text/html; charset=utf-8',
      cacheControl: asset ? 'public,max-age=31536000,immutable' : 'no-store' };
  });
  return { mode: 'AWS dashboard release; offline preparation only', bucket: deployment.dashboard_hosting.bucket,
    distributionId: deployment.dashboard_hosting.distribution_id, callbackUrl: env.VITE_QR_REDIRECT_URI, files: rows };
}

async function collect(directory, prefix = '') {
  const files = {};
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name), key = prefix + entry.name;
    if (entry.isDirectory()) Object.assign(files, await collect(path, key + '/'));
    else if (entry.isFile()) files[key] = await readFile(path);
    else throw new Error('Release cannot contain symbolic links or special files.');
  }
  return files;
}

async function main() {
  const options = { '--deployment': '.data/aws-deployment.json', '--output': '.data/dashboard-release' };
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 2) {
    if (!(args[i] in options) || !args[i + 1]) throw new Error('Use --deployment FILE and --output DIRECTORY.');
    options[args[i]] = args[i + 1];
  }
  const output = resolve(options['--output']), allowed = resolve(ROOT, '.data');
  if (!relative(allowed, output) || relative(allowed, output).startsWith('..') || output === allowed) throw new Error('Choose a new release directory inside ignored .data/.');
  try { await lstat(output); throw new Error('Release output already exists. Choose a new directory.'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const deployment = JSON.parse(await readFile(options['--deployment'], 'utf8')), env = hostedEnvironment(deployment);
  // Walk from the trusted project root so a symlink cannot redirect a release
  // into dist/, source files, or another directory outside ignored storage.
  const parents = ['.data', ...relative(allowed, dirname(output)).split('/').filter(Boolean)];
  let parent = ROOT;
  for (const component of parents) {
    parent = join(parent, component);
    try { await mkdir(parent); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    if (!(await lstat(parent)).isDirectory()) throw new Error('Release parent must be a directory, without symbolic links.');
  }
  const temporary = await mkdtemp(join(dirname(output), '.dashboard-build-'));
  try {
    // Only this CLI process is changed. envDir:false/configFile:false prevent
    // local .env files or Vite plugins from adding unexpected public variables.
    for (const key of Object.keys(process.env)) if (/^(VITE_|AWS_|QR_)/.test(key)) delete process.env[key];
    Object.assign(process.env, env);
    const { build } = await import('vite');
    const site = join(temporary, 'site');
    await build({ root: ROOT, configFile: false, envDir: false, publicDir: false,
      build: { outDir: site, emptyOutDir: true, sourcemap: false } });
    const html = await readFile(join(site, 'index.html'), 'utf8');
    if (!html.includes('<head>')) throw new Error('Release HTML has no head element.');
    await writeFile(join(site, 'index.html'), html.replace('<head>', `<head>\n<meta http-equiv="Content-Security-Policy" content="${hostingCsp(env)}">`));
    await writeFile(join(site, 'connect-origins.json'), JSON.stringify([env.VITE_QR_API_URL, env.VITE_QR_COGNITO_URL]));
    const manifest = releaseManifest(await collect(site), deployment);
    // Reserve the output atomically; never replace an existing release.
    await mkdir(output, { mode: 0o700 });
    await rename(site, join(output, 'site'));
    await writeFile(join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    console.log(`Prepared ${manifest.files.length} public files. No upload, invalidation or deployment was performed.`);
  } finally { await rm(temporary, { recursive: true, force: true }); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch(error => {
  console.error(error.message); process.exitCode = 1;
});
