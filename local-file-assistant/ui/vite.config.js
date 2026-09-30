import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Two pages: the main window (index.html) and the Ctrl+Shift+Space overlay (overlay.html).
// base './' so the built files load from file:// inside the packaged app.
export default defineConfig({
  base: './',
  server: { port: 5173, strictPort: true },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        overlay: resolve(__dirname, 'overlay.html'),
      },
    },
  },
});
