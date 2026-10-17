import { api } from '../api.js';
import { el, fileName, fmtBytes, fmtDate, notice, relPath, svg, ICONS, swatch } from '../components/common.js';

// Organize has three tabs:
//   Clean up       Tidy-up: a cleanup plan from names, sizes, dates and duplicate checks (no AI).
//   Move & delete  a small file browser: move, copy, rename, new folder, delete (to the Recycle Bin).
//   History        every change made here, with Undo.
// Nothing on disk changes until you press the button that says so. Moves, copies and new folders can
// be undone; deletes go to the Recycle Bin.

const TABS = [['clean', 'Clean up'], ['files', 'Move & delete'], ['history', 'History']];
const CHECKS = [['duplicates', 'Duplicates'], ['partial', 'Unfinished downloads'], ['old_large', 'Old large files'], ['empty', 'Empty folders'], ['sort', 'Sort by type']];
const SHOWN_PER_GROUP = 5;

const store = {
  get(k, d) {
    try {
      const v = JSON.parse(localStorage.getItem(`lfa.organize.${k}`));
      return v ?? d;
    } catch {
      return d;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem(`lfa.organize.${k}`, JSON.stringify(v));
    } catch { /* a remembered tab is only a convenience */ }
  },
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const sep = (path) => (path.includes('\\') ? '\\' : '/');
const join = (folder, name) => folder.replace(/[\\/]+$/, '') + sep(folder) + name;
const day = (mtime) => new Date(mtime * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

function checkbox(checked, label, onchange) {
  const box = el('input', { type: 'checkbox', class: 'check', 'aria-label': label, onchange });
  box.checked = checked;
  return box;
}

/** "3 items moved, 1 item copied, 2 items sent to the Recycle Bin" from apply results. */
function describe(results) {
  const ok = results.filter((r) => r.ok);
  const n = (op) => ok.filter((r) => r.op === op).length;
  const parts = [];
  if (n('move')) parts.push(`${plural(n('move'), 'item')} moved`);
  if (n('copy')) parts.push(`${plural(n('copy'), 'item')} copied`);
  if (n('rename')) parts.push('renamed');
  if (n('mkdir')) parts.push(`${plural(n('mkdir'), 'folder')} created`);
  if (n('delete')) parts.push(`${plural(n('delete'), 'item')} sent to the Recycle Bin`);
  const text = parts.length ? parts.join(', ') : 'Nothing changed';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function problemsOf(e) {
  const problems = e.detail?.problems || [];
  return `${e.message}${problems.length ? ` ${problems.join('; ')}` : ''}`;
}

/** The result of a change: what happened, what failed, and Undo when it can be undone. */
function resultNote(res, onUndone) {
  const failed = res.results.filter((r) => !r.ok);
  const undoable = res.results.some((r) => r.ok && r.op !== 'delete');
  const box = el('div', { class: `org-toast ${failed.length ? 'org-toast-error' : ''}`, role: 'status' });
  const text = el('span', {}, `${describe(res.results)}.${failed.length ? ` ${plural(failed.length, 'item')} couldn’t be changed.` : ''}`);
  const undo = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'Undo');
  undo.addEventListener('click', async () => {
    undo.disabled = true;
    try {
      const u = await api.undo(res.batch);
      const bad = u.results.filter((r) => !r.ok);
      text.textContent = bad.length ? `Undone, except: ${bad.map((r) => r.error).join('; ')}` : 'Undone.';
      undo.remove();
      onUndone?.();
    } catch (e) {
      text.textContent = e.message;
    }
  });
  box.append(text, undoable ? undo : '', el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Dismiss', onclick: () => box.remove() }, svg(ICONS.close, 12)));
  if (failed.length) box.append(el('div', { class: 'org-fails' }, ...failed.map((f) => el('div', {}, `${fileName(f.path)}: ${f.error}`))));
  return box;
}

/** A modal folder picker over the same file browser rules. Resolves to a folder path or null. */
function pickFolder({ title, verb, start, places, disallow = [] }) {
  return new Promise((resolve) => {
    let cwd = start;
    const dialog = el('dialog', { class: 'org-dialog', 'aria-label': title });
    const crumbs = el('div', { class: 'fm-crumbs' });
    const list = el('div', { class: 'fm-list fm-list-picker', 'aria-label': 'Folders' });
    const msg = el('div');
    const go = el('button', { type: 'button', class: 'btn btn-primary' }, `${verb} here`);
    const done = (value) => {
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    const low = (p) => p.toLowerCase();
    async function load(path) {
      msg.replaceChildren();
      try {
        const d = await api.browse(path);
        cwd = d.path;
        crumbs.replaceChildren(...d.crumbs.flatMap((c, i) => [i ? el('span', { class: 'fm-crumb-sep' }, '›') : '', el('button', { type: 'button', class: 'fm-crumb', onclick: () => load(c.path) }, c.name)]));
        const folders = d.entries.filter((e) => e.is_dir);
        list.replaceChildren(
          ...(d.parent ? [el('button', { type: 'button', class: 'fm-row fm-row-up', onclick: () => load(d.parent) }, el('span', { class: 'fm-up-arrow' }, svg(ICONS.arrow, 12)), el('span', { class: 'fm-name' }, 'Up one level'))] : []),
          ...folders.map((f) => el('button', { type: 'button', class: 'fm-row', onclick: () => load(f.path) }, swatch(f.path, 'sm', true), el('span', { class: 'fm-name' }, f.name))),
          ...(folders.length ? [] : [el('div', { class: 'quiet fm-empty' }, 'No folders inside. You can still put things here.')]),
        );
        const blocked = disallow.some((p) => low(p) === low(cwd) || low(cwd).startsWith(low(p) + sep(p)));
        go.disabled = blocked;
        go.title = blocked ? 'That’s where they already are, or inside a folder you’re moving.' : '';
      } catch (e) {
        msg.replaceChildren(notice(e.message, 'error'));
      }
    }
    const windowsPick = window.lfa?.pickFolder
      ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: async () => { const p = await window.lfa.pickFolder(); if (p) load(p); } }, 'Choose with Windows…')
      : '';
    go.addEventListener('click', () => done(cwd));
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); done(null); });
    dialog.append(
      el('div', { class: 'org-dialog-head' }, el('h2', {}, title), el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => done(null) }, svg(ICONS.close, 14))),
      el('div', { class: 'fm-places-row' }, ...places.map((p) => el('button', { type: 'button', class: 'chip chip-btn', onclick: () => load(p.path) }, p.label))),
      crumbs,
      msg,
      list,
      el('div', { class: 'org-dialog-foot' }, windowsPick, el('span', { class: 'spacer' }), el('button', { type: 'button', class: 'btn btn-outline', onclick: () => done(null) }, 'Cancel'), go),
    );
    document.body.append(dialog);
    dialog.showModal();
    load(start);
  });
}

// ---------------------------------------------------------------- Clean up

function cleanPane() {
  const folderSel = el('select', { id: 'tidy-root', class: 'field', 'aria-label': 'Folder to tidy' });
  const browseBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'Browse…');
  const run = el('button', { type: 'button', class: 'btn btn-primary' }, 'Find clutter');
  const chosen = new Set(store.get('checks', CHECKS.map(([k]) => k)));
  const largeMb = el('input', { type: 'number', class: 'field field-num', min: 1, value: store.get('largeMb', 100), 'aria-label': 'Large means over (MB)' });
  const oldDays = el('input', { type: 'number', class: 'field field-num', min: 1, value: store.get('oldDays', 90), 'aria-label': 'Old means not changed for (days)' });
  const thresholds = el('span', { class: 'org-thresholds' }, 'over', largeMb, 'MB, unchanged for', oldDays, 'days');
  const chips = CHECKS.map(([k, label]) => {
    const b = el('button', { type: 'button', class: 'chip chip-btn', 'aria-pressed': String(chosen.has(k)) }, label);
    b.addEventListener('click', () => {
      if (chosen.has(k)) chosen.delete(k);
      else chosen.add(k);
      b.setAttribute('aria-pressed', String(chosen.has(k)));
      thresholds.hidden = !chosen.has('old_large');
      run.disabled = !chosen.size || !folderSel.options.length;
      store.set('checks', [...chosen]);
    });
    return b;
  });
  thresholds.hidden = !chosen.has('old_large');
  const body = el('div', { class: 'org-body plan' });
  const count = el('span', { class: 'org-count' });
  const reject = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'Discard');
  const approve = el('button', { type: 'button', class: 'btn btn-primary btn-sm' }, 'Apply');
  const bar = el('div', { class: 'org-actionbar', hidden: true }, count, el('span', { class: 'spacer' }), reject, approve);
  let plan = null;
  let selected = new Set();
  let ticket = 0;

  const node = el(
    'div',
    { class: 'org-pane' },
    el('div', { class: 'toolbar' }, el('label', { class: 'toolbar-label', for: 'tidy-root' }, 'Folder'), folderSel, browseBtn, run),
    el('div', { class: 'toolbar org-checks' }, el('span', { class: 'toolbar-label' }, 'Look for'), ...chips, thresholds),
    body,
    bar,
  );

  function setBar() {
    bar.hidden = !plan || !plan.actions.length;
    const picked = plan ? plan.actions.filter((a) => selected.has(a)) : [];
    const frees = picked.filter((a) => a.op === 'delete').reduce((n, a) => n + (a.size || 0), 0);
    count.textContent = `${plural(picked.length, 'change')} selected${frees ? ` · frees ${fmtBytes(frees)}` : ''}`;
    approve.disabled = !picked.length;
  }

  function idle(message) {
    plan = null;
    selected = new Set();
    setBar();
    body.replaceChildren(message);
  }

  function typeBars(rows) {
    if (!rows?.length) return '';
    const top = rows.slice(0, 7);
    const max = Math.max(...top.map((r) => r.bytes), 1);
    return el(
      'section',
      { class: 'type-bars', 'aria-label': 'What takes the space' },
      el('div', { class: 'sources-h' }, 'What takes the space'),
      ...top.map((r) =>
        el(
          'div',
          { class: 'type-bar' },
          el('span', { class: 'type-name' }, r.type),
          el('span', { class: 'type-track' }, el('span', { class: 'type-fill', style: `width:${Math.max(2, (r.bytes / max) * 100)}%` })),
          el('span', { class: 'type-size' }, `${fmtBytes(r.bytes)} · ${plural(r.count, 'file')}`),
        ),
      ),
    );
  }

  function groupView(name, actions, root) {
    let expanded = actions.length <= SHOWN_PER_GROUP;
    const list = el('div', { class: 'plan-rows' });
    const all = checkbox(actions.every((a) => selected.has(a)), `Include all in ${name}`, () => {
      actions.forEach((a) => (all.checked ? selected.add(a) : selected.delete(a)));
      draw();
      setBar();
    });
    function draw() {
      const shown = expanded ? actions : actions.slice(0, SHOWN_PER_GROUP);
      list.replaceChildren(
        ...shown.map((a) =>
          el(
            'div',
            { class: 'plan-row' },
            el(
              'label',
              { class: 'plan-pick' },
              checkbox(selected.has(a), `Include ${fileName(a.path)}`, (e) => {
                if (e.target.checked) selected.add(a);
                else selected.delete(a);
                all.checked = actions.every((x) => selected.has(x));
                setBar();
              }),
              el('span', { class: 'plan-from' }, relPath(a.path, root)),
              svg(ICONS.arrow, 13),
              a.op === 'move'
                ? el('span', { class: 'plan-to' }, relPath(a.to, root))
                : el('span', { class: 'plan-to plan-trash' }, `Recycle Bin — ${a.reason || ''}${a.size && !/\d (B|KB|MB|GB)\b/.test(a.reason || '') ? ` · ${fmtBytes(a.size)}` : ''}`),
            ),
            el('button', { type: 'button', class: 'link-btn muted', 'aria-label': `Show ${fileName(a.path)} in its folder`, onclick: () => api.orgReveal(a.path).catch((e) => body.prepend(notice(e.message, 'error'))) }, 'Show'),
          ),
        ),
        expanded ? '' : el('button', { type: 'button', class: 'more', onclick: () => { expanded = true; draw(); } }, `+ ${actions.length - SHOWN_PER_GROUP} more`),
      );
    }
    draw();
    return el(
      'section',
      { class: 'plan-group' },
      el(
        'div',
        { class: 'plan-head' },
        el('h2', { class: 'plan-name' }, name.replaceAll('/', ' / ')),
        el('span', { class: 'count' }, plural(actions.length, name === 'Empty folders' ? 'folder' : 'file')),
        el('label', { class: 'include-all' }, all, 'Include all'),
      ),
      list,
    );
  }

  function showPlan(p) {
    plan = p;
    selected = new Set(p.actions.filter((a) => a.selected !== false));
    const groups = new Map();
    for (const a of p.actions) {
      const key = a.group || 'Other';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(a);
    }
    body.replaceChildren(
      el('div', { class: 'plan-summary' }, p.summary, p.scanned ? el('span', { class: 'quiet' }, ` ${plural(p.scanned, 'file')} looked at.`) : ''),
      typeBars(p.by_type),
      ...[...groups].map(([name, actions]) => groupView(name, actions, p.root)),
    );
    setBar();
  }

  run.addEventListener('click', async () => {
    run.disabled = true;
    const mine = ++ticket;
    store.set('largeMb', Number(largeMb.value) || 100);
    store.set('oldDays', Number(oldDays.value) || 90);
    idle(notice('Looking through the folder: names, sizes, dates and duplicate checks…'));
    try {
      const p = await api.tidy(folderSel.value || null, Number(largeMb.value) || 100, Number(oldDays.value) || 90, [...chosen]);
      if (mine === ticket) showPlan(p);
    } catch (e) {
      if (mine === ticket) idle(notice(e.message, 'error'));
    } finally {
      run.disabled = !chosen.size;
    }
  });

  browseBtn.addEventListener('click', async () => {
    const places = await api.places().then((r) => r.places, () => []);
    const start = folderSel.value || places[0]?.path;
    if (!start) return;
    const path = await pickFolder({ title: 'Choose a folder to tidy', verb: 'Tidy', start, places });
    if (!path) return;
    if (![...folderSel.options].some((o) => o.value === path)) folderSel.prepend(el('option', { value: path }, path));
    folderSel.value = path;
    run.disabled = !chosen.size;
  });

  reject.addEventListener('click', () => idle(notice('Plan discarded. Nothing was changed.')));

  approve.addEventListener('click', async () => {
    const actions = plan.actions.filter((a) => selected.has(a));
    // While approved changes run, nothing else can start, so the result stays in view.
    approve.disabled = reject.disabled = run.disabled = folderSel.disabled = browseBtn.disabled = true;
    count.replaceChildren(el('span', { class: 'org-spinner', 'aria-hidden': 'true' }), 'Applying… Large files can take a while.');
    count.setAttribute('role', 'status');
    try {
      const res = await api.apply(actions);
      idle(resultNote(res));
    } catch (e) {
      body.prepend(notice(problemsOf(e), 'error'));
      setBar();
    } finally {
      reject.disabled = folderSel.disabled = browseBtn.disabled = false;
      run.disabled = !chosen.size || !folderSel.options.length;
    }
  });

  idle(notice('Pick a folder (Downloads is the usual mess) and press Find clutter. Nothing changes until you press Apply; moves can be undone and deletes go to the Recycle Bin.'));
  api.tidyFolders().then(({ folders }) => {
    folderSel.replaceChildren(...folders.map((f) => el('option', { value: f.path }, f.key ? `${f.label} — ${f.path}` : f.path)));
    if (!folders.length) {
      run.disabled = true;
      idle(notice('Couldn’t find your Downloads or Desktop folder. Use Browse… to pick one.', 'error'));
    }
  }).catch((e) => idle(notice(e.message, 'error')));
  return { node };
}

// ---------------------------------------------------------------- Move & delete

function filesPane({ navigate }) {
  let places = [];
  let data = null;
  let selected = new Set(); // paths
  let anchor = null; // for shift-click ranges
  let showHidden = false;
  let busy = false; // a change is running: nothing else starts until it finishes

  const placesBox = el('nav', { class: 'fm-places', 'aria-label': 'Places' });
  const up = el('button', { type: 'button', class: 'icon-btn fm-up', 'aria-label': 'Up one level', title: 'Up one level (Backspace)' }, el('span', { class: 'fm-up-arrow' }, svg(ICONS.arrow, 14)));
  const crumbs = el('div', { class: 'fm-crumbs' });
  const filter = el('input', { type: 'search', class: 'field fm-filter', placeholder: 'Filter this folder', 'aria-label': 'Filter this folder' });
  const newFolder = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'New folder');
  const hiddenBox = el('label', { class: 'fm-hidden-toggle' }, checkbox(false, 'Show hidden items', (e) => { showHidden = e.target.checked; load(data?.path); }), 'Hidden items');
  const flash = el('div', { class: 'fm-flash' });
  const list = el('div', { class: 'fm-list', role: 'grid', tabindex: '0', 'aria-label': 'Files and folders', 'aria-multiselectable': 'true' });
  const head = el('div', { class: 'fm-head', role: 'row' });
  const foot = el('div', { class: 'fm-foot' });
  const selbar = el('div', { class: 'org-actionbar fm-selbar', hidden: true });

  const node = el(
    'div',
    { class: 'org-pane fm' },
    placesBox,
    el(
      'div',
      { class: 'fm-main' },
      el('div', { class: 'fm-toolbar' }, up, crumbs, el('span', { class: 'spacer' }), filter, newFolder, hiddenBox),
      flash,
      head,
      list,
      foot,
      selbar,
    ),
  );

  const visible = () => {
    const q = filter.value.trim().toLowerCase();
    return (data?.entries || []).filter((e) => !q || e.name.toLowerCase().includes(q));
  };
  const chosenEntries = () => (data?.entries || []).filter((e) => selected.has(e.path));
  const say = (n) => flash.replaceChildren(n || '');

  async function load(path) {
    if (!path) return false;
    try {
      const d = await api.browse(path, showHidden);
      if (data?.path !== d.path) selected = new Set();
      data = d;
      selected = new Set([...selected].filter((p) => d.entries.some((e) => e.path === p)));
      store.set('cwd', d.path);
      draw();
      return true;
    } catch (e) {
      const pcHint = /PC access/.test(e.message);
      say(
        el(
          'div',
          { class: 'notice notice-error' },
          e.message,
          pcHint && navigate ? el('button', { type: 'button', class: 'link-btn', onclick: () => navigate('index') }, ' Open Files & PC access') : '',
        ),
      );
      return false;
    }
  }

  function open(entry) {
    if (busy) return;
    if (entry.is_dir) {
      filter.value = '';
      say();
      load(entry.path);
    } else if (entry.runnable) {
      // Opening a program or script would run it; show it in File Explorer instead.
      say(notice(`${entry.name} is a program or script, so it isn’t opened from here. Showing it in File Explorer.`));
      api.orgReveal(entry.path).catch((e) => say(notice(e.message, 'error')));
    } else {
      api.orgOpen(entry.path).catch((e) => say(notice(e.message, 'error')));
    }
  }

  function toggleAll(on) {
    selected = on ? new Set(visible().map((e) => e.path)) : new Set();
    draw();
  }

  function pick(entry, ev) {
    if (busy) return;
    const rows = visible();
    if (ev.shiftKey && anchor) {
      const a = rows.findIndex((r) => r.path === anchor);
      const b = rows.findIndex((r) => r.path === entry.path);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        if (!(ev.ctrlKey || ev.metaKey)) selected = new Set();
        rows.slice(lo, hi + 1).forEach((r) => selected.add(r.path));
      }
    } else if (ev.ctrlKey || ev.metaKey) {
      if (selected.has(entry.path)) selected.delete(entry.path);
      else selected.add(entry.path);
      anchor = entry.path;
    } else {
      selected = new Set([entry.path]);
      anchor = entry.path;
    }
    draw();
  }

  function row(entry) {
    const isSel = selected.has(entry.path);
    const box = checkbox(isSel, `Select ${entry.name}`, () => {
      if (box.checked) selected.add(entry.path);
      else selected.delete(entry.path);
      anchor = entry.path;
      draw();
    });
    box.addEventListener('click', (e) => e.stopPropagation());
    const r = el(
      'div',
      { class: `fm-row ${isSel ? 'is-selected' : ''}`, role: 'row', 'aria-selected': String(isSel), 'data-path': entry.path, title: entry.is_dir ? 'Double-click to open the folder' : 'Double-click to open' },
      el('span', { class: 'fm-cell-check' }, box),
      swatch(entry.path, 'sm', entry.is_dir),
      el('span', { class: 'fm-name' }, entry.name),
      el('span', { class: 'fm-date' }, day(entry.mtime)),
      el('span', { class: 'fm-size' }, entry.is_dir ? '—' : fmtBytes(entry.size)),
    );
    if (entry.runnable) r.title = 'A program or script: double-click shows it in File Explorer (it isn’t run from here)';
    r.addEventListener('click', (e) => pick(entry, e));
    r.addEventListener('dblclick', () => open(entry));
    return r;
  }

  function draw() {
    if (!data) return;
    up.disabled = !data.parent;
    crumbs.replaceChildren(...data.crumbs.flatMap((c, i) => [i ? el('span', { class: 'fm-crumb-sep' }, '›') : '', el('button', { type: 'button', class: 'fm-crumb', onclick: () => load(c.path) }, c.name)]));
    const rows = visible();
    const allBox = checkbox(rows.length > 0 && rows.every((e) => selected.has(e.path)), 'Select all', (e) => toggleAll(e.target.checked));
    head.replaceChildren(el('span', { class: 'fm-cell-check' }, allBox), el('span', { class: 'fm-swatch-space' }), el('span', { class: 'fm-name' }, 'Name'), el('span', { class: 'fm-date' }, 'Modified'), el('span', { class: 'fm-size' }, 'Size'));
    list.replaceChildren(...(rows.length ? rows.map(row) : [el('div', { class: 'quiet fm-empty' }, filter.value ? 'Nothing here matches the filter.' : 'This folder is empty.')]));
    const files = data.entries.filter((e) => !e.is_dir);
    foot.textContent = [
      `${plural(data.entries.length - files.length, 'folder')}, ${plural(files.length, 'file')}`,
      files.length ? fmtBytes(files.reduce((n, e) => n + (e.size || 0), 0)) : '',
      data.more ? `${data.more} more not shown` : '',
      data.hidden ? `${data.hidden} hidden` : '',
    ].filter(Boolean).join(' · ');
    drawSelbar();
  }

  function drawSelbar(content) {
    const items = chosenEntries();
    selbar.hidden = !items.length && !busy;
    if (busy) {
      selbar.replaceChildren(el('span', { class: 'org-count', role: 'status' }, el('span', { class: 'org-spinner', 'aria-hidden': 'true' }), busy));
      return;
    }
    if (!items.length) return;
    if (content) {
      selbar.replaceChildren(...content);
      return;
    }
    const bytes = items.filter((e) => !e.is_dir).reduce((n, e) => n + (e.size || 0), 0);
    selbar.replaceChildren(
      el('span', { class: 'org-count' }, `${plural(items.length, 'item')} selected${bytes ? ` · ${fmtBytes(bytes)}` : ''}`),
      el('span', { class: 'spacer' }),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => transfer('move') }, 'Move to…'),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => transfer('copy') }, 'Copy to…'),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', disabled: items.length !== 1, onclick: () => rename() }, 'Rename'),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', disabled: items.length !== 1, onclick: () => api.orgReveal(items[0].path).catch((e) => say(notice(e.message, 'error'))) }, 'Show in Explorer'),
      el('button', { type: 'button', class: 'btn btn-danger btn-sm', onclick: () => askDelete() }, 'Delete'),
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Clear selection', onclick: () => toggleAll(false) }, svg(ICONS.close, 12)),
    );
  }

  const WORKING = { move: 'Moving', copy: 'Copying', rename: 'Renaming', mkdir: 'Creating the folder', delete: 'Sending to the Recycle Bin' };

  async function apply(actions) {
    if (busy) return;
    const n = actions.length;
    busy = `${WORKING[actions[0].op] || 'Working'}${n > 1 ? ` ${n} items` : ''}… Large files can take a while.`;
    for (const b of [newFolder, up]) b.disabled = true;
    list.setAttribute('aria-busy', 'true');
    node.classList.add('is-busy'); // places and the path bar wait too
    say();
    drawSelbar();
    try {
      const res = await api.apply(actions);
      selected = new Set();
      busy = false;
      say(resultNote(res, () => load(data.path)));
      await load(data.path);
    } catch (e) {
      busy = false;
      say(notice(problemsOf(e), 'error'));
      draw();
    } finally {
      busy = false;
      newFolder.disabled = false;
      list.removeAttribute('aria-busy');
      node.classList.remove('is-busy');
      draw();
    }
  }

  async function transfer(op) {
    const items = chosenEntries();
    if (!items.length) return;
    const verb = op === 'move' ? 'Move' : 'Copy';
    const target = await pickFolder({
      title: `${verb} ${items.length === 1 ? `“${items[0].name}”` : plural(items.length, 'item')} to…`,
      verb,
      start: data.path,
      places,
      disallow: [...(op === 'move' ? [data.path] : []), ...items.filter((e) => e.is_dir).map((e) => e.path)],
    });
    if (!target) return;
    apply(items.map((e) => ({ op, path: e.path, to_folder: target })));
  }

  function inlineInput(cell, value, label, onSave, selectStem) {
    const input = el('input', { type: 'text', class: 'field fm-rename', value, 'aria-label': label });
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('dblclick', (e) => e.stopPropagation());
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      const v = input.value.trim();
      if (save && v && v !== value) onSave(v);
      else if (save && v && onSave.always) onSave(v);
      else draw();
    };
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', () => finish(true));
    cell.replaceChildren(input);
    input.focus();
    const dot = selectStem ? value.lastIndexOf('.') : -1;
    input.setSelectionRange(0, dot > 0 ? dot : value.length);
  }

  function rename() {
    const [entry] = chosenEntries();
    const cell = entry && list.querySelector(`[data-path="${CSS.escape(entry.path)}"] .fm-name`);
    if (!cell) return;
    inlineInput(cell, entry.name, `New name for ${entry.name}`, (name) => apply([{ op: 'rename', path: entry.path, new_name: name }]), !entry.is_dir);
  }

  function askNewFolder() {
    if (!data) return;
    const taken = new Set(data.entries.map((e) => e.name.toLowerCase()));
    let name = 'New folder';
    for (let n = 2; taken.has(name.toLowerCase()); n++) name = `New folder (${n})`;
    const cell = el('span', { class: 'fm-name' });
    list.prepend(el('div', { class: 'fm-row is-selected' }, el('span', { class: 'fm-cell-check' }), swatch('', 'sm', true), cell, el('span', { class: 'fm-date' }), el('span', { class: 'fm-size' })));
    const save = (v) => apply([{ op: 'mkdir', path: join(data.path, v) }]);
    save.always = true; // keeping the suggested name still creates the folder
    inlineInput(cell, name, 'Name for the new folder', save, false);
  }

  function askDelete() {
    const items = chosenEntries();
    if (!items.length) return;
    const folders = items.filter((e) => e.is_dir).length;
    const yes = el('button', { type: 'button', class: 'btn btn-danger btn-sm' }, 'Delete');
    yes.addEventListener('click', () => apply(items.map((e) => ({ op: 'delete', path: e.path }))));
    drawSelbar([
      el('span', { class: 'org-count' }, `Send ${items.length === 1 ? `“${items[0].name}”` : plural(items.length, 'item')} to the Recycle Bin?${folders ? ' Folders go with everything inside them.' : ''}`),
      el('span', { class: 'spacer' }),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => drawSelbar() }, 'Cancel'),
      yes,
    ]);
    yes.focus();
  }

  up.addEventListener('click', () => { if (data?.parent && !busy) load(data.parent); });
  filter.addEventListener('input', draw);
  newFolder.addEventListener('click', askNewFolder);
  list.addEventListener('keydown', (e) => {
    if (e.target !== list || busy) return;
    if (e.key === 'Delete') { e.preventDefault(); askDelete(); }
    else if (e.key === 'F2') { e.preventDefault(); rename(); }
    else if (e.key === 'Backspace' && data?.parent) { e.preventDefault(); load(data.parent); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); toggleAll(true); }
    else if (e.key === 'Enter') { const items = chosenEntries(); if (items.length === 1) open(items[0]); }
    else if (e.key === 'Escape') toggleAll(false);
  });

  api.places().then(async (r) => {
    places = r.places;
    placesBox.replaceChildren(
      el('div', { class: 'sources-h' }, 'Places'),
      ...places.map((p) => el('button', { type: 'button', class: 'fm-place', title: p.path, onclick: () => { filter.value = ''; say(); load(p.path); } }, swatch(p.path, 'sm', true), el('span', {}, p.label))),
      r.pc_access ? '' : el('div', { class: 'quiet fm-place-note' }, 'Turn on PC access in Settings to work with any folder.'),
    );
    const remembered = store.get('cwd', null);
    if (!(remembered && (await load(remembered))) && places[0]) {
      say();
      await load(places[0].path);
    }
  }).catch((e) => say(notice(e.message, 'error')));

  return { node };
}

// ---------------------------------------------------------------- History

function historyPane() {
  const box = el('div', { class: 'org-body' }, el('div', { class: 'quiet' }, 'Loading…'));
  async function load() {
    try {
      const { batches } = await api.history();
      if (!batches.length) {
        box.replaceChildren(el('div', { class: 'empty' }, el('div', { class: 'empty-h' }, 'Nothing changed yet'), el('div', { class: 'empty-b' }, 'Changes you make on this page show up here, and the ones that can be undone get an Undo button.')));
        return;
      }
      box.replaceChildren(
        el(
          'div',
          { class: 'box' },
          ...batches.map((b) => {
            const parts = [];
            if (b.moves) parts.push(`${b.moves} moved`);
            if (b.copies) parts.push(`${b.copies} copied`);
            if (b.folders) parts.push(`${plural(b.folders, 'folder')} created`);
            if (b.trashed) parts.push(`${b.trashed} sent to the Recycle Bin`);
            const names = b.entries.slice(0, 3).map((e) => fileName(e.from || e.to)).join(', ') + (b.entries.length > 3 ? ` +${b.entries.length - 3} more` : '');
            const undo = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'Undo');
            undo.addEventListener('click', async () => {
              undo.disabled = true;
              try {
                const { results } = await api.undo(b.batch);
                const bad = results.filter((r) => !r.ok);
                await load();
                if (bad.length) box.prepend(notice(`Undone, except: ${bad.map((r) => r.error).join('; ')}`, 'error'));
              } catch (e) {
                box.prepend(notice(e.message, 'error'));
              }
            });
            return el(
              'div',
              { class: 'history-row' },
              el('div', { class: 'folder-main' }, el('div', {}, parts.join(', ')), el('div', { class: 'folder-meta' }, `${fmtDate(b.at)} · ${names}`)),
              b.undoable ? undo : el('span', { class: 'badge badge-outline', title: 'Restore these from the Recycle Bin' }, 'In the Recycle Bin'),
            );
          }),
        ),
      );
    } catch (e) {
      box.replaceChildren(notice(e.message, 'error'));
    }
  }
  load();
  return { node: el('div', { class: 'org-pane' }, box) };
}

// ---------------------------------------------------------------- page

export function render(container, { navigate } = {}) {
  let tab = store.get('tab', 'clean');
  if (!TABS.some(([k]) => k === tab)) tab = 'clean';
  const holder = el('div', { class: 'org-holder' });
  const seg = el('div', { class: 'seg', role: 'tablist', 'aria-label': 'Organize' });
  const buttons = TABS.map(([k, label]) => {
    const b = el('button', { type: 'button', class: 'seg-btn', role: 'tab', id: `organize-tab-${k}`, 'aria-selected': String(k === tab), 'aria-checked': String(k === tab) }, label);
    b.addEventListener('click', () => show(k));
    return b;
  });
  seg.append(...buttons);
  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'Organize'), el('div', { class: 'page-sub' }, 'Clean up folders, move and delete files. Nothing changes until you say so, and changes can be undone.')), seg),
    holder,
  );
  function show(k) {
    tab = k;
    store.set('tab', k);
    buttons.forEach((b, i) => {
      b.setAttribute('aria-selected', String(TABS[i][0] === k));
      b.setAttribute('aria-checked', String(TABS[i][0] === k));
    });
    const pane = k === 'clean' ? cleanPane() : k === 'files' ? filesPane({ navigate }) : historyPane();
    holder.replaceChildren(pane.node);
  }
  show(tab);
}
