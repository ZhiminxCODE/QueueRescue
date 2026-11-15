export function dashboardConfig(env, location) {
  const mode = env.VITE_QR_MODE || 'local';
  if (mode === 'local') return { mode };
  if (mode !== 'aws') throw new Error('Unknown workspace mode.');
  const region = env.VITE_QR_REGION || '';
  if (!/^[a-z]{2}-[a-z]+-\d$/.test(region)) throw new Error('AWS region is missing or invalid.');
  const apiUrl = env.VITE_QR_API_URL || '';
  const loginUrl = env.VITE_QR_COGNITO_URL || '';
  const escaped = region.replaceAll('-', '\\-');
  if (!new RegExp(`^https://[a-z0-9]+\\.execute-api\\.${escaped}\\.amazonaws\\.com$`).test(apiUrl)) throw new Error('Use the deployed regional API Gateway HTTPS endpoint.');
  if (!new RegExp(`^https://[a-z0-9-]+\\.auth\\.${escaped}\\.amazoncognito\\.com$`).test(loginUrl)) throw new Error('Use the deployed regional Cognito HTTPS domain.');
  const clientId = env.VITE_QR_COGNITO_CLIENT_ID || '';
  if (!/^[A-Za-z0-9_+]{1,128}$/.test(clientId)) throw new Error('Cognito public client ID is missing or invalid.');
  const redirect = new URL(env.VITE_QR_REDIRECT_URI || '');
  if ((redirect.protocol !== 'https:' && !(redirect.protocol === 'http:' && redirect.hostname === 'localhost')) || redirect.username || redirect.password || redirect.search || redirect.hash) throw new Error('Use an HTTPS callback or http://localhost with no query or fragment.');
  if (redirect.origin !== location.origin || redirect.pathname !== location.pathname) throw new Error('Open the dashboard at its configured sign-in callback URL.');
  const binding = [apiUrl, loginUrl, clientId, redirect.href].join('|');
  return Object.freeze({ mode, region, apiUrl, loginUrl, clientId, redirectUri: redirect.href, binding });
}
