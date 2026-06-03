import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/turn': 'http://localhost:8000',
      '/rooms': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/tickets': 'http://localhost:8000',
      // Tablet API subpaths only. The bare /tablet page stays a client route
      // served by the SPA, so these deeper prefixes must be matched explicitly.
      '/tablet/stream': 'http://localhost:8000',
      '/tablet/ack': 'http://localhost:8000',
      '/tablet/turn': 'http://localhost:8000',
      '/tablet/speak': 'http://localhost:8000',
      '/tablet/avatar': 'http://localhost:8000',
      '/tablet/listen': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
