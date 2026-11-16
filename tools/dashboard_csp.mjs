import { dashboardConfig } from '../src/cloud/config.js';

// Build metadata contains public origins only, using the same validation as login.
export function dashboardConnectOrigins(env) {
  if (!env.VITE_QR_MODE || env.VITE_QR_MODE === 'local') return [];
  const config = dashboardConfig(env, new URL(env.VITE_QR_REDIRECT_URI));
  return [config.apiUrl, config.loginUrl];
}
