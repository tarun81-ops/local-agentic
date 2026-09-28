const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const path = require('path');

const OVERLAY_WIDTH = 640;
const OVERLAY_HEIGHT = 64;
const OVERLAY_MAX_HEIGHT = 620;
const OVERLAY_TOP_OFFSET = 80;
const TOGGLE_SHORTCUT = 'CommandOrControl+Shift+Space';

const BACKEND_DIR = path.join(__dirname, '..', '..', 'backend');
const BACKEND_PYTHON = path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe');
const API_TOKEN = crypto.randomBytes(32).toString('hex');

const IS_DEV = process.argv.includes('--dev');
const DEV_SERVER_URL = 'http://localhost:5173';

let overlay = null;
let mainWindow = null;
let backendProcess = null;

function startBackend() {
  backendProcess = spawn(BACKEND_PYTHON, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8756'], {
    cwd: BACKEND_DIR,
    env: { ...process.env, API_TOKEN, OLLAMA_KEEP_ALIVE: '-1' },
  });
  backendProcess.stdout.on('data', (data) => process.stdout.write(`[backend] ${data}`));
  backendProcess.stderr.on('data', (data) => process.stderr.write(`[backend] ${data}`));
  backendProcess.on('exit', (code) => {
    if (code !== null && code !== 0) console.error(`backend process exited with code ${code}`);
    backendProcess = null;
  });
}

function stopBackend() {
  if (backendProcess) backendProcess.kill();
}

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

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    title: 'Local File Assistant',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (IS_DEV) {
    // concurrently starts Vite and Electron together, so the dev server may not
    // be listening yet — retry until it is.
    mainWindow.webContents.on('did-fail-load', (_event, _code, _desc, _url, isMainFrame) => {
      if (isMainFrame) setTimeout(() => mainWindow?.loadURL(DEV_SERVER_URL), 500);
    });
    mainWindow.loadURL(DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => { mainWindow = null; });
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
  startBackend();
  createOverlay();
  createMainWindow();

  const registered = globalShortcut.register(TOGGLE_SHORTCUT, toggleOverlay);
  if (!registered) {
    console.error(`Could not register global shortcut ${TOGGLE_SHORTCUT} (already taken by another app).`);
  }
});

ipcMain.on('overlay:hide', hideOverlay);
ipcMain.on('overlay:resize', (_event, height) => resizeOverlay(height));
ipcMain.handle('overlay:get-api-token', () => API_TOKEN);

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  stopBackend();
});

// ponytail: no tray icon yet — quitting on window-all-closed is fine while
// the overlay is the only window this app has.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
