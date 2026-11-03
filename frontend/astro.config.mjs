import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  build: { assets: 'assets' },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
});
