import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Load .env files so VITE_API_TARGET works when running `npm run dev` directly
  const env = loadEnv(mode, process.cwd(), '')

  // Proxy /api requests to the backend.
  // Priority: VITE_API_TARGET env var → default to 5003 (matches dev.sh)
  const apiTarget = env.VITE_API_TARGET || 'http://localhost:5003'

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 3003,
      // Allow custom hostnames (useful when accessing via Termius or other machines)
      allowedHosts: ['bandit', 'bandit.shik', 'localhost'],
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        }
      }
    }
  }
})