import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Cible de l'API/orchestrateur pour le proxy de dev.
// - En local (hors Docker) : défaut 127.0.0.1:17490.
// - En Docker (override dev) : VITE_API_TARGET=http://orchestrator:17490.
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:17490'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    host: '0.0.0.0',
    allowedHosts: ['abs.virtuaworld.org'],
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true
      },
      '/audio': {
        target: apiTarget,
        changeOrigin: true
      }
    }
  }
})
