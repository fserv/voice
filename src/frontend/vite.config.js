import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Proxy API calls to the Flask backend so the browser can use same-origin /api/*.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // bind 0.0.0.0 so a Windows/WSL2 browser can reach it
    port: 5173,
    proxy: {
      '/api': 'http://localhost:5001',
    },
  },
});
