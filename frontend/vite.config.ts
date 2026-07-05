/** Vite dev/preview configuration for the local AdaptIQ frontend. */

import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(() => {
  return {
    server: {
      port: 5173,
      host: '127.0.0.1',
      fs: {
        strict: true,
        allow: [__dirname],
      },
    },
    preview: {
      port: 5173,
      host: '127.0.0.1',
    },
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
  };
});
