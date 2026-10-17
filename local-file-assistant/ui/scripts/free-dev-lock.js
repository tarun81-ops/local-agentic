// Runs automatically before `npm run dev` (npm "predev" hook).
//
// The app lives in the system tray and holds Electron's single-instance lock. When a previous
// dev run is stopped abruptly (a crashed `npm run dev`, a closed terminal, concurrently's -k kill
// on Windows), its Electron process can survive in the tray. The next `electron . --dev` then
// fails the lock, quits silently with code 0, and concurrently takes Vite down with it.
//
// This script stops any leftover Electron started from THIS project's node_modules, and nothing
// else (the installed app, VS Code and other Electron apps are left alone).

const { spawnSync } = require('child_process');
const path = require('path');

const electronDir = path.resolve(__dirname, '..', 'node_modules', 'electron', 'dist');

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function windowsPids() {
  const dir = electronDir.replace(/'/g, "''");
  const script =
    'Get-Process -Name electron -ErrorAction SilentlyContinue | ' +
    `Where-Object { $_.Path -and $_.Path.StartsWith('${dir}', [System.StringComparison]::OrdinalIgnoreCase) } | ` +
    'ForEach-Object { $_.Id }';
  const r = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8' });
  return (r.stdout || '').split(/\s+/).filter((s) => /^\d+$/.test(s));
}

try {
  if (process.platform === 'win32') {
    const pids = windowsPids();
    if (pids.length) {
      console.log(`[predev] stopping ${pids.length} leftover dev Electron process(es) holding the single-instance lock`);
      for (const pid of pids) {
        // /T also stops child processes (renderers and the Python backend) so none keep the port.
        spawnSync('taskkill', ['/PID', pid, '/T', '/F'], { windowsHide: true });
      }
      sleep(800); // let the lock release
    }
  } else {
    const r = spawnSync('pkill', ['-f', path.join(electronDir, 'electron')]);
    if (r.status === 0) {
      console.log('[predev] stopped a leftover dev Electron process');
      sleep(800);
    }
  }
} catch (err) {
  // Never block `npm run dev` because the cleanup itself failed.
  console.warn(`[predev] cleanup skipped: ${err.message}`);
}
