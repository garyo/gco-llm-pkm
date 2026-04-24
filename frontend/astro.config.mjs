// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Packages that shared/editor imports. Aliased to this app's node_modules so
// Vite can resolve them when bundling files from outside the frontend/ root.
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

// https://astro.build/config
export default defineConfig({
  // Build output to Flask templates directory
  outDir: '../templates',
  build: {
    // Output as a single HTML file
    format: 'file',
    assets: '_astro', // Assets in templates/_astro/
  },

  // Development server configuration
  server: {
    port: 4321,
  },

  vite: {
    resolve: {
      alias: {
        '@pkm/editor': path.resolve(__dirname, '../shared/editor'),
        ...Object.fromEntries(
          SHARED_EDITOR_DEPS.map((p) => [p, path.resolve(__dirname, 'node_modules', p)]),
        ),
      },
    },
    plugins: [
      tailwindcss(),
      viteStaticCopy({
        targets: [
          {
            src: 'node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js',
            dest: 'vad',
          },
          {
            src: 'node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx',
            dest: 'vad',
          },
          {
            src: 'node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx',
            dest: 'vad',
          },
          {
            src: 'node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm',
            dest: 'vad',
          },
          {
            src: 'node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs',
            dest: 'vad',
          },
        ],
      }),
    ],
    server: {
      // Proxy API calls to Flask backend
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/query': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/login': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/verify-token': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/sessions': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/health': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/transcribe': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/assets': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/auth': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
      }
    }
  }
});
