import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { dashboardConfig } from '../src/cloud/config.js';

export function dashboardEnvironment(deployment, redirectUri) {
  const values = {
    VITE_QR_MODE: 'aws', VITE_QR_REGION: deployment.region,
    VITE_QR_API_URL: deployment.api_url, VITE_QR_COGNITO_URL: deployment.cognito_login_url,
    VITE_QR_COGNITO_CLIENT_ID: deployment.cognito_client_id, VITE_QR_REDIRECT_URI: redirectUri,
  };
  if (Object.values(values).some(value => typeof value !== 'string')) throw new Error('Deployment outputs are incomplete.');
  dashboardConfig(values, new URL(redirectUri));
  // Allowlist public fields; never spread the deployment or process environment.
  return '# Public dashboard connection settings. Never put AWS credentials here.\n' +
    Object.entries(values).map(([key, value]) => key + '=' + JSON.stringify(value)).join('\n') + '\n';
}

async function main() {
  const args = process.argv.slice(2);
  const options = { '--deployment': '.data/aws-deployment.json', '--redirect-uri': 'http://localhost:5173/', '--output': '.env.local' };
  for (let index = 0; index < args.length; index += 2) {
    if (!(args[index] in options) || !args[index + 1]) throw new Error('Use --deployment FILE, --redirect-uri URL, and/or --output FILE.');
    options[args[index]] = args[index + 1];
  }
  const deployment = JSON.parse(await readFile(options['--deployment'], 'utf8'));
  await writeFile(options['--output'], dashboardEnvironment(deployment, options['--redirect-uri']), { flag: 'wx', mode: 0o600 });
  console.log('Created public browser configuration in', options['--output']);
  console.log('Review callback URLs and API CORS in Terraform, then restart Vite or rebuild the dashboard.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch(error => {
  console.error(error.code === 'EEXIST' ? 'Output already exists. Review it before choosing a new output file.' : error.message);
  process.exitCode = 1;
});
