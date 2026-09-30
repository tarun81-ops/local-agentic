const { app, BrowserWindow, Menu, Tray, dialog, globalShortcut, ipcMain, nativeImage, screen } = require('electron');
const { spawn, spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');

const TOGGLE_SHORTCUT = 'CommandOrControl+Shift+Space';
const SHORTCUT_LABEL = 'Ctrl+Shift+Space';
const IS_DEV = process.argv.includes('--dev');
const START_HIDDEN = process.argv.includes('--hidden'); // launched at login: live in the tray
const DEV_SERVER_URL = 'http://localhost:5173';
const API_TOKEN = crypto.randomBytes(32).toString('hex');

let port = null;
let backend = null;
let mainWindow = null;
let overlay = null;
let tray = null;
let quitting = false;
let restarts = 0;
const MAX_RESTARTS = 3;

// ---------- logging ----------
// The packaged app has no console, so main-process errors and everything the backend prints
// go to %APPDATA%\Local File Assistant\logs\main.log (trimmed when it passes 5 MB).

const LOG_LIMIT = 5 * 1024 * 1024;
let logFile = null;

function openLog() {
  try {
    const dir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    logFile = path.join(dir, 'main.log');
    if (fs.existsSync(logFile) && fs.statSync(logFile).size > LOG_LIMIT) {
      fs.renameSync(logFile, `${logFile}.1`);
    }
  } catch {
    logFile = null;
  }
}

function log(line) {
  const text = `${new Date().toISOString()} ${String(line).trimEnd()}\n`;
  process.stdout.write(text);
  if (logFile) fs.appendFile(logFile, text, () => {});
}

process.on('uncaughtException', (err) => log(`uncaught: ${err.stack || err}`));
process.on('unhandledRejection', (err) => log(`unhandled rejection: ${err?.stack || err}`));

// ---------- backend ----------

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port: p } = srv.address();
      srv.close(() => resolve(p));
    });
  });
}

function backendCommand() {
  if (app.isPackaged) {
    // Frozen with PyInstaller by scripts\build-backend.ps1, shipped via extraResources.
    const exe = path.join(process.resourcesPath, 'backend', 'lfa-backend.exe');
    return { cmd: exe, args: [], cwd: path.dirname(exe) };
  }
  const dir = path.join(__dirname, '..', '..', 'backend');
  return { cmd: path.join(dir, '.venv', 'Scripts', 'python.exe'), args: ['run.py'], cwd: dir };
}

function startBackend() {
  const { cmd, args, cwd } = backendCommand();
  if (!fs.existsSync(cmd)) {
    log(`backend not found at ${cmd}. Run scripts\\setup.ps1 (dev) or rebuild the installer.`);
    return;
  }
  const child = spawn(cmd, args, {
    cwd,
    env: { ...process.env, PORT: String(port), API_TOKEN },
    windowsHide: true,
  });
  backend = child;
  child.stdout.on('data', (d) => log(`[backend] ${d}`));
  child.stderr.on('data', (d) => log(`[backend] ${d}`));
  child.on('error', (err) => log(`backend failed to start (${cmd}): ${err.message}`));
  child.on('exit', (code) => {
    if (backend === child) backend = null;
    if (quitting) return;
    log(`backend exited with code ${code}`);
    // Same port and token on restart, so open pages keep working once it is back.
    if (restarts < MAX_RESTARTS) {
      restarts += 1;
      log(`restarting backend (attempt ${restarts}/${MAX_RESTARTS})`);
      setTimeout(startBackend, 1000 * restarts);
    }
  });
}

function stopBackend() {
  if (!backend) return;
  const pid = backend.pid;
  backend = null;
  if (process.platform === 'win32' && pid) {
    // A venv python.exe on Windows is a launcher that starts the real interpreter as a
    // child; killing only the launcher would leave the backend running and holding the port.
    spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true });
  } else if (pid) {
    try { process.kill(pid); } catch { /* already gone */ }
  }
}

// ---------- windows ----------

function load(win, page) {
  if (IS_DEV) {
    // Vite and Electron start together; retry until the dev server is listening.
    win.webContents.on('did-fail-load', (_e, _c, _d, _u, isMainFrame) => {
      if (isMainFrame) setTimeout(() => win.loadURL(`${DEV_SERVER_URL}/${page}`), 500);
    });
    win.loadURL(`${DEV_SERVER_URL}/${page}`);
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', page));
  }
}

const webPreferences = {
  preload: path.join(__dirname, 'preload.js'),
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
};

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    title: 'Local File Assistant',
    backgroundColor: '#F5F3EE',
    show: false,
    webPreferences,
  });
  load(mainWindow, 'index.html');
  mainWindow.once('ready-to-show', () => {
    if (!START_HIDDEN) mainWindow.show();
  });
  // Closing the window keeps the app (and the Ctrl+Shift+Space overlay) running in the tray.
  mainWindow.on('close', (e) => {
    if (!quitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createOverlay() {
  overlay = new BrowserWindow({
    frame: false,
    transparent: true,
    hasShadow: false,
    alwaysOnTop: true,
    resizable: false,
    movable: false,
    skipTaskbar: true,
    show: false,
    webPreferences,
  });
  // Above full-screen apps and on every virtual desktop.
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  load(overlay, 'overlay.html');
  overlay.on('blur', hideOverlay);
}

function showMain(page) {
  mainWindow.show();
  mainWindow.focus();
  if (page) mainWindow.webContents.send('navigate', page);
}

function showOverlay() {
  // The overlay covers the display the cursor is on; the page draws the dimmed backdrop.
  const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  overlay.setBounds(display.workArea);
  overlay.show();
  overlay.focus();
  overlay.webContents.send('overlay:shown');
}

function hideOverlay() {
  if (overlay?.isVisible()) overlay.hide();
}

function toggleOverlay() {
  if (overlay.isVisible()) hideOverlay();
  else showOverlay();
}

function createTray() {
  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'tray.png')));
  tray.setToolTip('Local File Assistant');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open Local File Assistant', click: () => showMain() },
      { label: `Search files (${SHORTCUT_LABEL})`, click: showOverlay },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]),
  );
  tray.on('click', () => showMain());
}

// ---------- IPC ----------

ipcMain.handle('app:config', () => ({ port, token: API_TOKEN }));
ipcMain.handle('app:shortcut', () => SHORTCUT_LABEL);
ipcMain.handle('dialog:pick-folder', async (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  const result = await dialog.showOpenDialog(win, { title: 'Choose a folder to index', properties: ['openDirectory'] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('app:get-login', () => ({
  enabled: app.isPackaged && app.getLoginItemSettings().openAtLogin,
  supported: app.isPackaged,
}));
ipcMain.handle('app:set-login', (_e, enabled) => {
  if (!app.isPackaged) return false;
  app.setLoginItemSettings({ openAtLogin: Boolean(enabled), args: ['--hidden'] });
  return true;
});
ipcMain.on('overlay:hide', hideOverlay);
ipcMain.on('main:open', (_e, page) => {
  hideOverlay();
  showMain(page);
});

// ---------- lifecycle ----------

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => showMain());

  app.on('web-contents-created', (_e, contents) => {
    // The app never navigates away or opens pop-ups; refuse anything that tries.
    contents.on('will-navigate', (e, url) => {
      if (!url.startsWith(DEV_SERVER_URL) && !url.startsWith('file://')) e.preventDefault();
    });
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    openLog();
    port = await freePort();
    startBackend();
    createMainWindow();
    createOverlay();
    createTray();
    if (!globalShortcut.register(TOGGLE_SHORTCUT, toggleOverlay)) {
      log(`Could not register ${SHORTCUT_LABEL}: another app is using it.`);
    }
  });

  app.on('before-quit', () => {
    quitting = true;
  });

  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    stopBackend();
  });

  // The tray keeps the app alive; quitting is explicit (tray menu > Quit).
  app.on('window-all-closed', (e) => e.preventDefault());
}
