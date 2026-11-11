import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

const api = process.env.API_URL || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(process.cwd(), 'src') } },
  server: { port: 5173, proxy: { '/api': { target: api, changeOrigin: true } } },
})
