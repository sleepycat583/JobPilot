/** 第 3 章 Playwright 本地验证专用 Vite 代理，避免占用开发服务器端口。 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8011', changeOrigin: true },
      '/v1': { target: 'http://127.0.0.1:8011', changeOrigin: true },
    },
  },
})