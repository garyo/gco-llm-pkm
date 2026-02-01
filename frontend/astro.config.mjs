// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';

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
