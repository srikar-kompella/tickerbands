import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// Port 5174 + API on 8001: the Portfolio app owns 5173/8000 and both apps run
// simultaneously (DESIGN_QUESTIONS Q10).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5174,
    strictPort: true,
    // Demo mode (./demo.sh): the tunnel's Host header (xyz.trycloudflare.com)
    // must be allowed or Vite's DNS-rebinding guard blocks every request.
    // Normal runs keep the strict default.
    allowedHosts: process.env.TICKERLENS_DEMO ? true : undefined,
    proxy: { '/api': { target: 'http://localhost:8001', changeOrigin: true } },
  },
})
