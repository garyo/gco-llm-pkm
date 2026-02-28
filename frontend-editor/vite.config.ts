import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  base: '/editor/',
  plugins: [tailwindcss()],
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
