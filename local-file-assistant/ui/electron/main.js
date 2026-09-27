const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const path = require('path');

const OVERLAY_WIDTH = 640;
const OVERLAY_HEIGHT = 64;
const OVERLAY_MAX_HEIGHT = 620;
const OVERLAY_TOP_OFFSET = 80;
const TOGGLE_SHORTCUT = 'CommandOrControl+Space';

let overlay = null;

function createOverlay() {
  const { width } = screen.getPrimaryDisplay().workAreaSize;

  overlay = new BrowserWindow({
    width: OVERLAY_WIDTH,
    height: OVERLAY_HEIGHT,
    x: Math.round((width - OVERLAY_WIDTH) / 2),
    y: OVERLAY_TOP_OFFSET,
    frame: false,
    transparent: true,
    hasShadow: false,
    alwaysOnTop: true,
    resizable: false,
    movable: false,
    skipTaskbar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Above fullscreen apps too, and visible from every virtual desktop —
  // this is what makes it "hover over everything" rather than just other windows.
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  overlay.loadFile(path.join(__dirname, 'overlay.html'));
  overlay.on('blur', hideOverlay);
}

function showOverlay() {
  if (!overlay) createOverlay();
  overlay.show();
  overlay.focus();
}

function hideOverlay() {
  if (!overlay) return;
  if (overlay.isVisible()) overlay.hide();
  overlay.setSize(OVERLAY_WIDTH, OVERLAY_HEIGHT); // reset to the idle bar for the next summon
}

function resizeOverlay(height) {
  if (!overlay) return;
  const clamped = Math.max(OVERLAY_HEIGHT, Math.min(OVERLAY_MAX_HEIGHT, Math.round(height)));
  overlay.setSize(OVERLAY_WIDTH, clamped);
}

function toggleOverlay() {
  if (overlay && overlay.isVisible()) hideOverlay();
  else showOverlay();
}

app.whenReady().then(() => {
  createOverlay();

  const registered = globalShortcut.register(TOGGLE_SHORTCUT, toggleOverlay);
  if (!registered) {
    console.error(`Could not register global shortcut ${TOGGLE_SHORTCUT} (already taken by another app).`);
  }
});

ipcMain.on('overlay:hide', hideOverlay);
ipcMain.on('overlay:resize', (_event, height) => resizeOverlay(height));

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

// ponytail: overlay-only shell for now, no backend process management —
// add spawning the FastAPI backend + auth token handoff here once
// routes_chat/search/organize are actually implemented (they currently
// just raise NotImplementedError), and give the app a tray icon so
// window-all-closed doesn't quit an overlay-only app.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
