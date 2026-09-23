import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const contractsRoot = fileURLToPath(new URL('../contracts', import.meta.url));

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
    fs: { allow: mode === 'mock' ? [frontendRoot, contractsRoot] : [frontendRoot] },
  },
}));
