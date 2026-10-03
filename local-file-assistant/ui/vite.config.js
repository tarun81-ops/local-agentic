import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Two pages: the main window (index.html) and the Ctrl+Shift+Space overlay (overlay.html).
// base './' so the built files load from file:// inside the packaged app.
export default defineConfig({
  base: './',
  // 5173 is what Electron's dev mode loads and the backend's CORS list allows. PORT lets a
  // second copy (the Claude preview) run beside `npm run dev` instead of fighting over it.
  server: { port: Number(process.env.PORT) || 5173, strictPort: true },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The microphone worklet must stay a real file: the page CSP forbids data: scripts.
    assetsInlineLimit: (file) => (file.endsWith('pcm-worklet.js') ? false : undefined),
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        overlay: resolve(__dirname, 'overlay.html'),
      },
    },
  },
});
