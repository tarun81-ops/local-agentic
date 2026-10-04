import { api } from '../api.js';
import { badge, el, fmtBytes, fmtDate, notice, svg, ICONS } from '../components/common.js';
import { mountDashboard } from '../components/dashboard.js';

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

const PRESETS = [
  ['College (PDF, DOCX, PPTX)', { extensions: ['pdf', 'docx', 'pptx'], exclude_globs: ['~$*'], ocr_only: false, caption_images: null }],
  ['Code and notes (md, txt, csv)', { extensions: ['md', 'txt', 'csv'], exclude_globs: ['node_modules', '.git'], ocr_only: false, caption_images: null }],
  ['Personal (OCR only, no captions)', { extensions: [], exclude_globs: [], ocr_only: true, caption_images: false }],
];
const STYLE_OPTIONS = [['', 'USE MY DEFAULT'], ['concise', 'CONCISE'], ['detailed', 'DETAILED'], ['study', 'STUDY'], ['simple', 'SIMPLE']];
const CAPTION_OPTIONS = [['', 'FOLLOW THE GLOBAL SETTING'], ['true', 'ON'], ['false', 'OFF']];

export function render(container) {
  const openDrawers = new Set(); // folders whose FOLDER SETTINGS are showing; survives the scan-progress refresh
  const add = el('button', { type: 'button', class: 'btn btn-primary btn-sm' }, '+ ADD FOLDER');
  const overview = el('div');
  const folders = el('div', { class: 'box' });
  const problems = el('div');
  const history = el('div');
  const flash = el('div');
  let timer = null;
  let updateOverview = null;

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('h1', { class: 'page-title' }, 'INDEX')),
    el(
      'div',
      { class: 'page-body sections' },
      flash,
      el('section', {}, sectionHead('00', 'OVERVIEW'), overview),
      el('section', {}, sectionHead('01', 'INDEXED FOLDERS', add), folders),
      el('section', {}, sectionHead('02', 'PROBLEMS'), problems),
      el('section', {}, sectionHead('03', 'HISTORY'), history),
    ),
  );

  const say = (msg, tone) => flash.replaceChildren(msg ? notice(msg, tone) : '');

  /** Per-folder rules: label, answer style, file types, exclusions, captions. Saving rescans the folder. */
  function drawer(r) {
    const p = r.profile || {};
    const field = (label, control) => el('div', { class: 'kv kv-plain' }, el('label', {}, label), control);
    const label = el('input', { type: 'text', class: 'field', maxlength: 60, value: p.label || '', 'aria-label': 'Folder label' });
    const style = el('select', { class: 'field field-mono', 'aria-label': 'Answer style for this folder' }, STYLE_OPTIONS.map(([v, t]) => el('option', { value: v, selected: v === (p.answer_style || '') }, t)));
    const exts = el('input', { type: 'text', class: 'field', placeholder: 'pdf, docx, md  (empty = all types)', value: (p.extensions || []).join(', '), 'aria-label': 'File types to index' });
    const globs = el('textarea', { class: 'field', rows: 3, placeholder: 'One pattern per line, e.g. node_modules or *.tmp', 'aria-label': 'Exclusions' });
    globs.value = (p.exclude_globs || []).join('\n');
    const caption = el('select', { class: 'field field-mono', 'aria-label': 'Image captions for this folder' }, CAPTION_OPTIONS.map(([v, t]) => el('option', { value: v, selected: v === (p.caption_images == null ? '' : String(p.caption_images)) }, t)));
    const ocr = el('input', { type: 'checkbox', class: 'toggle', role: 'switch', 'aria-label': 'OCR only, no captions' });
    ocr.checked = Boolean(p.ocr_only);
    const out = el('div');
    const fill = (v) => {
      exts.value = v.extensions.join(', ');
      globs.value = v.exclude_globs.join('\n');
      ocr.checked = v.ocr_only;
      caption.value = v.caption_images == null ? '' : String(v.caption_images);
    };
    const profile = () => ({
      label: label.value,
      answer_style: style.value,
      extensions: exts.value.split(',').map((x) => x.trim()).filter(Boolean),
      exclude_globs: globs.value.split('\n').map((x) => x.trim()).filter(Boolean),
      caption_images: caption.value === '' ? null : caption.value === 'true',
      ocr_only: ocr.checked,
    });
    const save = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SAVE & RESCAN');
    save.addEventListener('click', async () => {
      try {
        const res = await api.saveFolderProfile(r.path, profile());
        say(res.rescan === 'started' ? 'Saved. Rescanning this folder now.' : 'Saved. A scan is already running: press RESCAN when it finishes.');
        openDrawers.delete(r.path);
      } catch (e) {
        out.replaceChildren(notice(e.message, 'error'));
        return;
      }
      refresh();
    });
    return el(
      'div',
      { class: 'folder-settings stack' },
      el('div', { class: 'row-gap' }, ...PRESETS.map(([t, v]) => el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => fill(v) }, t))),
      field('LABEL', label),
      field('ANSWER STYLE', style),
      field('FILE TYPES', exts),
      field('EXCLUDE', globs),
      field('IMAGE CAPTIONS', caption),
      field('OCR ONLY (NO CAPTIONS)', ocr),
      el('div', { class: 'quiet' }, 'Applies after a rescan. Saving starts one; files the new rules exclude are removed from the index (never from your disk). Captions apply to new or changed images.'),
      el('div', { class: 'row-gap' }, save, el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => rescan(r.path) }, 'RESCAN')),
      out,
    );
  }

  function folderRow(r) {
    const [label, tone] = STATES[r.state] || STATES.not_scanned;
    const meta =
      r.state === 'scanning'
        ? `Scanning — ${r.progress.seen.toLocaleString()} files checked, ${r.progress.indexed.toLocaleString()} new or changed${
            r.progress.files_per_min ? ` · ${Math.round(r.progress.files_per_min).toLocaleString()} files/min` : ''
          }`
        : `${r.files.toLocaleString()} files · ${fmtBytes(r.bytes)}${r.last_scan ? ` · last scan ${fmtDate(r.last_scan)}` : ''}${
            // Indexed without vectors (low memory, or the embedding model was down): rescan to finish.
            r.keyword_only ? ` · ${r.keyword_only.toLocaleString()} keyword-only, rescan to add semantic search` : ''
          }`;
    const remove = el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Stop indexing ${r.path}` }, svg(ICONS.close, 14));
    const actions = el(
      'div',
      { class: 'row-actions' },
      r.state === 'scanning'
        ? el('button', { type: 'button', class: 'link-btn', onclick: stopScan, 'aria-label': `Stop scanning ${r.path}` }, 'STOP')
        : r.state !== 'missing'
          ? el('button', { type: 'button', class: 'link-btn', onclick: () => rescan(r.path), 'aria-label': `Rescan ${r.path}` }, 'RESCAN')
          : '',
      el('button', { type: 'button', class: 'link-btn', 'aria-expanded': String(openDrawers.has(r.path)), 'aria-label': `Folder settings for ${r.path}`, onclick: () => { openDrawers.has(r.path) ? openDrawers.delete(r.path) : openDrawers.add(r.path); refresh(); } }, 'FOLDER SETTINGS'),
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
      {},
      el(
        'div',
        { class: 'folder-row' },
        el('div', { class: 'folder-main' }, r.profile?.label ? el('div', { class: 'folder-label' }, r.profile.label.toUpperCase()) : '', el('div', { class: 'folder-path' }, r.path), el('div', { class: 'folder-meta' }, meta)),
        badge(label, tone),
        actions,
        r.state === 'scanning' ? el('div', { class: 'progress', 'aria-hidden': 'true' }, el('div')) : '',
      ),
      openDrawers.has(r.path) ? drawer(r) : '',
    );
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const needFiles = !updateOverview; // file-type chart: fetched once, not on every 1s scan poll
      const [{ roots }, status, { batches }, filesResult] = await Promise.all([
        api.roots(),
        api.indexStatus(),
        api.history(),
        needFiles ? api.files() : Promise.resolve(null),
      ]);
      const overviewData = { roots, batches, files: filesResult?.files };
      if (!updateOverview) updateOverview = mountDashboard(overview, overviewData);
      else updateOverview(overviewData);
      const editing = folders.querySelector('.folder-settings :focus'); // don't rebuild a form being typed in
      if (!editing) folders.replaceChildren(
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
