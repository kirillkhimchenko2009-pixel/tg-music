import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base './' — обязательно для GitHub Pages из подкаталога и для /v2 на туннеле
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
