import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:9876'
const proxiedBackendRoute = { target: apiProxyTarget, changeOrigin: true }

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/static/app/',
  server: {
    proxy: {
      '/api': proxiedBackendRoute,
      '/explore': proxiedBackendRoute,
      '/static/kg.js': proxiedBackendRoute,
      '/static/kg-config.js': proxiedBackendRoute,
    },
  },
  build: {
    outDir: '../static/app',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
