const { app, BrowserWindow, Menu, Notification, Tray, clipboard, dialog, globalShortcut, ipcMain, nativeImage, screen, session } = require('electron');
const { spawn, spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');

const DEFAULT_SHORTCUT = 'CommandOrControl+Shift+Space';
// Rarely taken: Ctrl+Shift+V is plain-text paste in browsers, and Ctrl+Alt+Space is Claude desktop's quick entry.
const DEFAULT_VOICE_SHORTCUT = 'CommandOrControl+Shift+Alt+V';
const OVERLAY_W = 520;
const OVERLAY_H = 640;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Windows shows toast notifications only for an app with a registered id (matches build.appId).
app.setAppUserModelId('com.localfileassistant.app');
const IS_DEV = process.argv.includes('--dev');
const START_HIDDEN = process.argv.includes('--hidden'); // launched at login: live in the tray
const DEV_SERVER_URL = 'http://localhost:5173';
const API_TOKEN = crypto.randomBytes(32).toString('hex');

let port = null;
let backend = null;
let mainWindow = null;
let overlay = null;
let tray = null;
let shortcut = DEFAULT_SHORTCUT;
let voiceShortcut = DEFAULT_VOICE_SHORTCUT;
let overlayPinned = false;
let lastContext = null; // what was in front when the overlay was summoned: {app, title, hwnd, selection}
let summoning = false;
let dragging = false; // a file is being dragged out of the panel: it must not hide on blur
let dragIcon = null;
const dragAllowed = new Set(); // paths the panel was just offered and may drag out
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
    icon: path.join(__dirname, 'app-icon.png'),
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
  // A compact panel, not a full-screen scrim: pages underneath stay visible and usable
  // (the file you find can be dragged into a website's upload box).
  overlay = new BrowserWindow({
    width: OVERLAY_W,
    height: OVERLAY_H,
    minWidth: 420,
    minHeight: 360,
    frame: false,
    backgroundColor: '#F5F3EE',
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: true,
    show: false,
    webPreferences,
  });
  // Above full-screen apps and on every virtual desktop.
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  load(overlay, 'overlay.html');
  overlay.on('blur', () => {
    if (!overlayPinned && !dragging) hideOverlay();
  });
}

function showMain(page) {
  mainWindow.show();
  mainWindow.focus();
  if (page) mainWindow.webContents.send('navigate', page);
}

function placeOverlay() {
  // Right edge of the display the cursor is on. A pinned panel stays where the user put it.
  if (overlayPinned) return;
  const wa = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
  overlay.setBounds({ x: wa.x + wa.width - OVERLAY_W - 16, y: wa.y + 16, width: OVERLAY_W, height: Math.min(OVERLAY_H, wa.height - 32) });
}

async function backendPost(pathname, body) {
  const r = await fetch(`http://127.0.0.1:${port}${pathname}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${API_TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    signal: AbortSignal.timeout(2500),
  });
  if (!r.ok) throw new Error(`${pathname} ${r.status}`);
  return r.json();
}

// ponytail: restores every clipboard format Electron can read as a buffer; exotic formats may not round-trip.
const saveClipboard = () => clipboard.availableFormats().map((f) => [f, clipboard.readBuffer(f)]);
function restoreClipboard(saved) {
  clipboard.clear();
  for (const [f, buf] of saved) clipboard.writeBuffer(f, buf);
}

/** Asks the backend to copy the selection in the window in front, reads it off the clipboard,
 *  and puts the user's clipboard back. Returns null when there is nothing to capture. */
async function captureContext() {
  if (BrowserWindow.getFocusedWindow() || !port) return null; // our own window is in front
  const SENTINEL = `lfa-${crypto.randomUUID()}`;
  const saved = saveClipboard();
  clipboard.writeText(SENTINEL);
  try {
    const info = await backendPost('/context/capture');
    if (info.ignored) return { ignored: true };
    if (!info.hwnd) return null;
    await sleep(200); // let the app finish its copy
    const text = clipboard.readText();
    return { app: info.app, title: info.title, hwnd: info.hwnd, selection: text === SENTINEL ? '' : text };
  } catch (err) {
    log(`context capture failed: ${err.message}`);
    return null;
  } finally {
    restoreClipboard(saved);
  }
}

async function showOverlay() {
  if (summoning) return;
  summoning = true;
  try {
    lastContext = await captureContext(); // before we take focus
    placeOverlay();
    overlay.show();
    overlay.focus();
    const shown = lastContext && !lastContext.ignored ? { app: lastContext.app, title: lastContext.title, selection: lastContext.selection } : null;
    overlay.webContents.send('overlay:shown', shown);
  } finally {
    summoning = false;
  }
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
      { label: `Search files (${shortcutLabel()})`, click: showOverlay },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]),
  );
  tray.on('click', () => showMain());
}

// ---------- reminders ----------

const shownNotifications = new Set(); // keep references so toasts aren't garbage-collected before the click

function showNotice(type, event) {
  if (!Notification.isSupported()) return;
  const n =
    type === 'reminder'
      ? new Notification({ title: event.missed ? 'Missed reminder' : 'Reminder', body: event.task.title })
      : new Notification({ title: event.title, body: event.body }); // briefing, nudge
  shownNotifications.add(n);
  n.on('click', () => showMain('tasks'));
  n.on('close', () => shownNotifications.delete(n));
  n.show();
}

/** Follows the backend's /events/stream for as long as the app runs, reconnecting if the backend restarts. */
async function listenForReminders() {
  let lastError = '';
  while (!quitting) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/events/stream`, { headers: { Authorization: `Bearer ${API_TOKEN}` } });
      if (!r.ok) throw new Error(`status ${r.status}`);
      lastError = ''; // connected: the next outage is worth a log line
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const m = /^event: (reminder|briefing|nudge)\ndata: (.*)$/m.exec(block);
          if (m) showNotice(m[1], JSON.parse(m[2]));
        }
      }
    } catch (err) {
      if (!quitting && err.message !== lastError) log(`reminder stream: ${err.message}`); // once per outage, not every retry
      lastError = err.message;
    }
    await sleep(3000);
  }
}

// Reminders only fire while the app runs, so the first launch that has them turns on "launch at startup".
function applyLoginDefault() {
  if (!app.isPackaged) return;
  const marker = path.join(app.getPath('userData'), 'login-default-applied');
  if (fs.existsSync(marker)) return;
  app.setLoginItemSettings({ openAtLogin: true, args: ['--hidden'] });
  try {
    fs.writeFileSync(marker, '1');
  } catch { /* try again next launch */ }
}

// ---------- shortcuts ----------

const shortcutFile = () => path.join(app.getPath('userData'), 'shortcuts.json');
const label = (accelerator) => accelerator.replace('CommandOrControl', 'Ctrl');
const shortcutLabel = () => label(shortcut);

function loadShortcuts() {
  try {
    const saved = JSON.parse(fs.readFileSync(shortcutFile(), 'utf8'));
    return { toggle: saved.toggle || DEFAULT_SHORTCUT, voice: saved.voice || DEFAULT_VOICE_SHORTCUT };
  } catch {
    return { toggle: DEFAULT_SHORTCUT, voice: DEFAULT_VOICE_SHORTCUT };
  }
}

/** Moves one of the two global shortcuts (toggle or voice). Keeps the old one, and says why, if the new one can't be taken. */
function setShortcut(which, next) {
  if (!next) return { ok: false, error: 'Type a shortcut, e.g. Ctrl+Alt+Space.' };
  const current = which === 'voice' ? voiceShortcut : shortcut;
  const other = which === 'voice' ? shortcut : voiceShortcut;
  if (next === current) return { ok: true, shortcut: label(current) };
  if (next === other) return { ok: false, error: `${next} is already used for the other shortcut.` };
  let registered = false;
  try {
    registered = globalShortcut.register(next, which === 'voice' ? voiceToggle : toggleOverlay);
  } catch { /* not a valid accelerator */ }
  if (!registered) return { ok: false, error: `${next} isn't available: it's invalid or another app uses it.` };
  globalShortcut.unregister(current);
  if (which === 'voice') voiceShortcut = next;
  else shortcut = next;
  try {
    fs.writeFileSync(shortcutFile(), JSON.stringify({ toggle: shortcut, voice: voiceShortcut }));
  } catch (err) {
    log(`could not save shortcut: ${err.message}`);
  }
  return { ok: true, shortcut: label(next) };
}

/** The voice shortcut: bring the panel up (capturing context as usual), then start or stop listening. */
async function voiceToggle() {
  if (!overlay.isVisible()) await showOverlay();
  overlay.webContents.send('overlay:voice-toggle');
}

/** The microphone is for our own pages only, and audio only (never the camera). */
function allowMicrophone() {
  const ours = (url) => typeof url === 'string' && (url.startsWith(DEV_SERVER_URL) || url.startsWith('file://'));
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback, details) => {
    callback(permission === 'media' && ours(details.requestingUrl) && (details.mediaTypes || []).every((t) => t === 'audio'));
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission, origin) => permission === 'media' && ours(origin));
}

// ---------- IPC ----------

ipcMain.handle('app:config', () => ({ port, token: API_TOKEN }));
ipcMain.handle('app:shortcut', () => shortcutLabel());
ipcMain.handle('app:shortcuts', () => ({ toggle: shortcutLabel(), voice: label(voiceShortcut) }));
ipcMain.handle('shortcuts:set', (_e, { which, accelerator }) => setShortcut(which === 'voice' ? 'voice' : 'toggle', String(accelerator || '').trim()));
ipcMain.on('overlay:drag-allow', (e, { paths, replace }) => {
  if (e.sender !== overlay.webContents) return;
  if (replace) dragAllowed.clear();
  for (const p of paths || []) if (typeof p === 'string') dragAllowed.add(p);
});
// A page can't start a native file drag itself. Only files it was just offered (and the user has
// not marked "ask every time" without confirming) can be dragged into another app's window.
ipcMain.on('overlay:start-drag', (e, file) => {
  if (e.sender !== overlay.webContents || !dragAllowed.has(file) || !fs.existsSync(file)) return;
  dragIcon ||= nativeImage.createFromPath(path.join(__dirname, 'app-icon.png')).resize({ width: 40, height: 40 });
  dragging = true;
  setTimeout(() => {
    dragging = false;
    if (!overlayPinned && !overlay.isFocused()) hideOverlay();
  }, 20000);
  e.sender.startDrag({ file, icon: dragIcon });
});
ipcMain.on('overlay:pin', (_e, pinned) => {
  overlayPinned = Boolean(pinned);
});
// Pastes an answer into the window the overlay was opened over. If that fails the text is left
// on the clipboard and the overlay comes back, so nothing is lost.
ipcMain.handle('overlay:insert', async (_e, text) => {
  const hwnd = lastContext?.hwnd;
  if (!hwnd || typeof text !== 'string' || !text) return false;
  const saved = saveClipboard();
  clipboard.writeText(text);
  hideOverlay();
  await sleep(150);
  try {
    await backendPost('/context/insert', { hwnd });
    setTimeout(() => restoreClipboard(saved), 600);
    return true;
  } catch (err) {
    log(`insert failed: ${err.message}`);
    overlay.show();
    return false;
  }
});
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
    applyLoginDefault();
    listenForReminders();
    ({ toggle: shortcut, voice: voiceShortcut } = loadShortcuts());
    if (!globalShortcut.register(shortcut, toggleOverlay)) {
      log(`Could not register ${shortcutLabel()}: another app is using it.`);
    }
    if (!globalShortcut.register(voiceShortcut, voiceToggle)) {
      log(`Could not register ${label(voiceShortcut)}: another app is using it.`);
    }
    allowMicrophone();
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
