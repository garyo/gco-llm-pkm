import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Packages that shared/editor imports. Aliased to this app's node_modules so
// Vite can resolve them when bundling files outside frontend-editor/.
const SHARED_EDITOR_DEPS = [
  'codemirror',
  '@codemirror/view',
  '@codemirror/state',
  '@codemirror/language',
  '@codemirror/lang-markdown',
  '@codemirror/theme-one-dark',
  '@replit/codemirror-emacs',
  '@lezer/highlight',
];

export default defineConfig({
  base: '/editor/',
  plugins: [tailwindcss()],
  resolve: {
    alias: {
      '@pkm/editor': path.resolve(__dirname, '../shared/editor'),
      ...Object.fromEntries(
        SHARED_EDITOR_DEPS.map((p) => [p, path.resolve(__dirname, 'node_modules', p)]),
      ),
    },
  },
  build: {
    outDir: '../editor-dist',
    emptyOutDir: true,
  },
  server: {
    port: 4322,
    proxy: {
      '/api': 'http://localhost:8000',
      '/login': 'http://localhost:8000',
      '/verify-token': 'http://localhost:8000',
      '/assets': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
    },
  },
});
