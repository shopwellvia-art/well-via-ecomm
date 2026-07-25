import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: true,
    // Local dev runs bare-metal (`npm run dev`), so this falls back to
    // localhost. Set VITE_API_PROXY if your backend listens elsewhere.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/media': {
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Never inline fonts as base64 data: URIs. The production CSP (nginx.conf)
    // has no `font-src data:`, so an inlined font would be blocked at runtime.
    // Emitting every weight as a separate file keeps them under `default-src
    // 'self'`. Other assets keep Vite's default size-based inlining.
    assetsInlineLimit: (filePath) =>
      /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
  },
});
