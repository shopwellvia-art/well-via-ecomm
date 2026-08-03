import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Unit tests default to plain node — the suite mostly targets pure logic
// (stores, helpers, status rules), not rendered components. Tests that need a
// real DOM (e.g. lib/__tests__/pageMeta.test.js, which asserts on
// document.head) opt in per-file with `// @vitest-environment jsdom`. The
// setup file stubs Web Storage because zustand's persist middleware touches
// localStorage at store-creation time.
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
