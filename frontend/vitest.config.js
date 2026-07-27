import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Unit tests run in plain node — the suite targets pure logic (stores, helpers,
// status rules), not rendered components, so no jsdom is needed. The setup file
// stubs Web Storage because zustand's persist middleware touches localStorage
// at store-creation time.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{js,jsx}'],
    setupFiles: ['./src/test/setup.js'],
  },
});
