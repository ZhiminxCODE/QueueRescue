import { defineConfig, loadEnv } from 'vite';
import { dashboardConnectOrigins } from './tools/dashboard_csp.mjs';

export default defineConfig(({ mode }) => ({
  plugins: [{
    name: 'dashboard-connect-policy',
    generateBundle() {
      const origins = dashboardConnectOrigins({ ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env });
      this.emitFile({ type: 'asset', fileName: 'connect-origins.json', source: JSON.stringify(origins) });
    },
  }],
  server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist' },
}));
