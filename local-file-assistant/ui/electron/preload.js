const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('overlay', {
  hide: () => ipcRenderer.send('overlay:hide'),
  resize: (height) => ipcRenderer.send('overlay:resize', height),
  getApiToken: () => ipcRenderer.invoke('overlay:get-api-token'),
});
