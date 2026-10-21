import { api } from '../api.js';
import { badge, el, fmtDate, notice } from '../components/common.js';

// Files & PC access: the one switch that lets the assistant find and read your files. The undo history
// lives on Organize → History. The app keeps only file names and locations; a file is opened and read at the
// moment you ask about it, and nothing from inside your files is stored.
const NEVER_OPENED = 'Windows and Program Files, AppData, developer folders (node_modules, .git), links, and anything that looks like a password, key or credentials file (.ssh, *.pem, *.key, .env, passwords…).';

export function render(container) {
  const flash = el('div');
  const pcBox = el('div', { class: 'box box-pad stack' }, el('div', { class: 'quiet' }, 'Loading…'));
  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'Files & PC access'), el('div', { class: 'page-sub' }, 'File names only — files are read when you ask, nothing is copied'))),
    el(
      'div',
      { class: 'page-body sections' },
      flash,
      el('section', {}, el('div', { class: 'section-h' }, el('h2', {}, 'PC access')), pcBox),
      el('section', {}, el('div', { class: 'section-h' }, el('h2', {}, 'Undo history')), el('div', { class: 'quiet' }, 'Moves, copies and deletes made in Organize are listed, with Undo, on Organize → History.')),
    ),
  );

  const say = (msg, tone) => flash.replaceChildren(msg ? notice(msg, tone) : '');
  let timer = null;
  let disposed = false; // set when you leave the tab: a reply still in flight must not start a new poll

  const act = async (fn, okMsg) => {
    try {
      await fn();
      if (okMsg) say(okMsg);
    } catch (e) {
      say(e.message, 'error');
    }
    refreshPc();
  };

  function driveChecks(available, chosen, onChange) {
    return el(
      'div',
      { class: 'row-gap' },
      ...available.map((d) => {
        const c = el('input', { type: 'checkbox', class: 'check', 'aria-label': `Cover drive ${d}`, value: d });
        c.checked = chosen.includes(d);
        if (onChange) c.addEventListener('change', onChange);
        return el('label', { class: 'chip' }, c, ` ${d}`);
      }),
    );
  }
  const picked = (box) => [...box.querySelectorAll('input:checked')].map((c) => c.value);

  async function refreshPc() {
    clearTimeout(timer);
    let s;
    try {
      s = await api.pcStatus();
    } catch (e) {
      pcBox.replaceChildren(notice(e.message, 'error'));
      return;
    }
    if (disposed) return;

    if (!s.enabled) {
      const drives = driveChecks(s.available_drives, s.available_drives.slice(0, 1));
      const turnOn = el('button', { type: 'button', class: 'btn btn-primary' }, 'Turn on');
      turnOn.addEventListener('click', () => {
        const chosen = picked(drives);
        if (!chosen.length) return say('Pick at least one drive.', 'error');
        act(() => api.pcSettings({ enabled: true, drives: chosen }), 'PC access is on. Listing your file names now; Find and Chat can use them in a minute or two.');
      });
      pcBox.replaceChildren(
        el('div', { class: 'row-gap' }, badge('Off', 'outline'), el('span', {}, 'Find and Chat can’t see your files yet.')),
        el('div', { class: 'quiet' }, 'Turn this on so you can ask for any file by describing it. The app lists file names and locations on the drives you pick; when you ask about a file, it opens that file and reads it right then. Nothing is copied and nothing leaves this PC.'),
        el('div', { class: 'quiet' }, 'Drives:'),
        drives,
        el('div', { class: 'row-gap' }, turnOn),
      );
      return;
    }

    const prog = s.progress || {};
    const status = s.running
      ? `Updating the list — ${(prog.seen || 0).toLocaleString()} files checked`
      : `Knows ${s.files.toLocaleString()} file${s.files === 1 ? '' : 's'}${s.last_scan ? ` · list updated ${fmtDate(s.last_scan)}` : ''}`;
    const drives = driveChecks(s.available_drives, s.drives, () => {
      const chosen = picked(drives);
      if (!chosen.length) return say('Keep at least one drive, or turn PC access off.', 'error');
      act(() => api.pcSettings({ drives: chosen }), 'Drives saved. Updating the file list.');
    });
    pcBox.replaceChildren(
      el('div', { class: 'row-gap' }, badge(s.running ? 'Updating' : 'On', s.running ? 'red' : 'blue'), el('span', { role: 'status' }, status)),
      s.running ? el('div', { class: 'progress', 'aria-hidden': 'true' }, el('div')) : '',
      s.scan_error ? notice(`The last update stopped: ${s.scan_error}`, 'error') : '',
      el('div', { class: 'quiet' }, 'Drives:'),
      drives,
      el('div', { class: 'quiet' }, `Never opened: ${NEVER_OPENED}`),
      el(
        'div',
        { class: 'row-gap' },
        s.running
          ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => act(() => api.pcStop()) }, 'Stop')
          : el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => act(() => api.pcScan(), 'Updating the file list.') }, 'Update list now'),
        el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => act(() => api.pcSettings({ enabled: false }), 'PC access is off. Find and Chat no longer see your files.') }, 'Turn off'),
      ),
    );
    if (s.running && !disposed) timer = setTimeout(refreshPc, 1500);
  }

  refreshPc();
  return () => {
    disposed = true;
    clearTimeout(timer);
  };
}
