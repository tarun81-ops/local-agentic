import { api } from '../api.js';
import { badge, el, fmtBytes, fmtDate, notice, svg, ICONS } from '../components/common.js';

const STATES = {
  up_to_date: ['UP TO DATE', 'blue'],
  scanning: ['SCANNING', 'red'],
  queued: ['QUEUED', 'outline'],
  not_scanned: ['NOT SCANNED', 'outline'],
  missing: ['FOLDER MISSING', 'outline'],
};

function sectionHead(n, title, ...extra) {
  return el('div', { class: 'section-h' }, el('h2', {}, el('span', { class: 'n' }, `${n} —`), ` ${title}`), ...extra);
}

export function render(container) {
  const add = el('button', { type: 'button', class: 'btn btn-primary btn-sm' }, '+ ADD FOLDER');
  const folders = el('div', { class: 'box' });
  const problems = el('div');
  const history = el('div');
  const flash = el('div');
  let timer = null;

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('h1', { class: 'page-title' }, 'INDEX')),
    el(
      'div',
      { class: 'page-body sections' },
      flash,
      el('section', {}, sectionHead('01', 'INDEXED FOLDERS', add), folders),
      el('section', {}, sectionHead('02', 'PROBLEMS'), problems),
      el('section', {}, sectionHead('03', 'HISTORY'), history),
    ),
  );

  const say = (msg, tone) => flash.replaceChildren(msg ? notice(msg, tone) : '');

  function folderRow(r) {
    const [label, tone] = STATES[r.state] || STATES.not_scanned;
    const meta =
      r.state === 'scanning'
        ? `Scanning — ${r.progress.seen.toLocaleString()} files checked, ${r.progress.indexed.toLocaleString()} new or changed${
            r.progress.files_per_min ? ` · ${Math.round(r.progress.files_per_min).toLocaleString()} files/min` : ''
          }`
        : `${r.files.toLocaleString()} files · ${fmtBytes(r.bytes)}${r.last_scan ? ` · last scan ${fmtDate(r.last_scan)}` : ''}`;
    const remove = el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Stop indexing ${r.path}` }, svg(ICONS.close, 14));
    const actions = el(
      'div',
      { class: 'row-actions' },
      r.state === 'scanning'
        ? el('button', { type: 'button', class: 'link-btn', onclick: stopScan, 'aria-label': `Stop scanning ${r.path}` }, 'STOP')
        : r.state !== 'missing'
          ? el('button', { type: 'button', class: 'link-btn', onclick: () => rescan(r.path), 'aria-label': `Rescan ${r.path}` }, 'RESCAN')
          : '',
      remove,
    );
    remove.addEventListener('click', () => {
      actions.replaceChildren(
        el('span', { class: 'confirm-q' }, 'Stop indexing? Files stay on disk.'),
        el('button', { type: 'button', class: 'link-btn', onclick: () => removeRoot(r.path) }, 'YES'),
        el('button', { type: 'button', class: 'link-btn muted', onclick: refresh }, 'NO'),
      );
    });
    return el(
      'div',
      { class: 'folder-row' },
      el('div', { class: 'folder-main' }, el('div', { class: 'folder-path' }, r.path), el('div', { class: 'folder-meta' }, meta)),
      badge(label, tone),
      actions,
      r.state === 'scanning' ? el('div', { class: 'progress', 'aria-hidden': 'true' }, el('div')) : '',
    );
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const [{ roots }, status, { batches }] = await Promise.all([api.roots(), api.indexStatus(), api.history()]);
      folders.replaceChildren(
        ...(roots.length
          ? roots.map(folderRow)
          : [el('div', { class: 'empty-row' }, 'No folders yet. Add one and it will be indexed and kept up to date automatically.')]),
      );
      problems.replaceChildren(
        status.errors.length
          ? el(
              'div',
              { class: 'box' },
              status.errors.map((e) => el('div', { class: 'problem-row' }, el('div', { class: 'folder-path' }, e.path), el('div', { class: 'problem-msg' }, e.error))),
            )
          : el('div', { class: 'quiet' }, 'No problems. Files that can’t be read will be listed here with the reason.'),
      );
      history.replaceChildren(
        batches.length
          ? el(
              'div',
              { class: 'box' },
              batches.map((b) =>
                el(
                  'div',
                  { class: 'history-row' },
                  el('div', { class: 'folder-main' }, el('div', {}, fmtDate(b.at)), el('div', { class: 'folder-meta' }, `${b.moves} moved · ${b.trashed} sent to Recycle Bin`)),
                  b.moves ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => undo(b.batch) }, 'UNDO') : badge('RECYCLE BIN ONLY'),
                ),
              ),
            )
          : el('div', { class: 'quiet' }, 'Reorganizations you approve show up here and can be undone.'),
      );
      if (status.state === 'running') timer = setTimeout(refresh, 1000);
    } catch (e) {
      say(e.message, 'error');
    }
  }

  async function rescan(path) {
    try {
      await api.rescan(path);
      say('');
    } catch (e) {
      say(e.message, 'error');
    }
    refresh();
  }

  async function stopScan() {
    try {
      await api.cancelIndex();
      say('Stopping after the current file. Files indexed so far are kept; RESCAN continues from there.');
    } catch (e) {
      say(e.message, 'error');
    }
    refresh();
  }

  async function removeRoot(path) {
    try {
      const { removed } = await api.removeRoot(path);
      say(`Stopped indexing ${path}. ${removed} files removed from the index; nothing on disk changed.`);
    } catch (e) {
      say(e.message, 'error');
    }
    refresh();
  }

  async function undo(batch) {
    try {
      const { results } = await api.undo(batch);
      const bad = results.filter((r) => !r.ok);
      say(
        bad.length
          ? `Moved ${results.length - bad.length} back; ${bad.length} couldn’t be: ${bad.map((r) => r.error).join('; ')}`
          : `Moved ${results.length} file${results.length === 1 ? '' : 's'} back.`,
        bad.length ? 'error' : 'neutral',
      );
    } catch (e) {
      say(e.message, 'error');
    }
    refresh();
  }

  add.addEventListener('click', async () => {
    const path = await window.lfa.pickFolder();
    if (!path) return;
    try {
      await api.addRoot(path);
      say(`Indexing ${path}. You can keep working while it scans.`);
    } catch (e) {
      say(e.message, 'error');
    }
    refresh();
  });

  refresh();
  return () => clearTimeout(timer);
}
