import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = process.env.CHECKSTOCK_UI_BACKEND || 'http://127.0.0.1:8000'
const proxy = {
  '^/api/auth(?:/|$)': { target: backend, changeOrigin: false },
  '/static/': { target: backend },
}

export default defineConfig({
  plugins: [react()],
  publicDir: false,
  server: { host: 'localhost', port: 5189, strictPort: true, proxy },
  preview: { host: 'localhost', port: 5189, strictPort: true, proxy },
})
