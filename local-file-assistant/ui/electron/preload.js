const { contextBridge, ipcRenderer } = require('electron');

// The only bridge between the pages and Electron. Pages get the backend's port and token
// from here and can ask for a folder picker; they can't reach Node or the filesystem.
contextBridge.exposeInMainWorld('lfa', {
  config: () => ipcRenderer.invoke('app:config'),
  shortcut: () => ipcRenderer.invoke('app:shortcut'),
  pickFolder: () => ipcRenderer.invoke('dialog:pick-folder'),
  getLaunchAtStartup: () => ipcRenderer.invoke('app:get-login'),
  setLaunchAtStartup: (enabled) => ipcRenderer.invoke('app:set-login', enabled),
  openMain: (page) => ipcRenderer.send('main:open', page),
  hideOverlay: () => ipcRenderer.send('overlay:hide'),
  onNavigate: (cb) => ipcRenderer.on('navigate', (_e, page) => cb(page)),
  onOverlayShown: (cb) => ipcRenderer.on('overlay:shown', () => cb()),
});
