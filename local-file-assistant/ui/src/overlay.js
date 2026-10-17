import '@fontsource/archivo/400.css';
import '@fontsource/archivo/500.css';
import '@fontsource/archivo/700.css';
import '@fontsource/archivo/900.css';
import '@fontsource/space-mono/400.css';
import '@fontsource/space-mono/700.css';
import './style.css';

import { api, waitForBackend } from './api.js';
import { applyCached, loadAppearance } from './appearance.js';
import { ask } from './components/chat-log.js';
import { micButton, readAloud, speak } from './components/mic.js';
import { badge, el, fmtBytes, notice, swatch } from './components/common.js';
import { resultRow } from './pages/search.js';
import { mascot, setMood } from './components/mascot.js';

applyCached();

const input = document.getElementById('query');
const body = document.getElementById('overlay-body');
const status = document.getElementById('overlay-status');
const pin = document.getElementById('pin');
const keys = document.getElementById('overlay-keys');
const panel = document.getElementById('panel');
const strip = document.querySelector('.ov-strip');
const foot = document.getElementById('overlay-footer');
const chipBox = document.getElementById('context-chip');
const tabs = [...document.querySelectorAll('.tab')];
const lumi = mascot({ size: 26 });
document.getElementById('mascot-slot').replaceChildren(lumi);

let mode = 'search';
let seq = 0;
let timer = null; // pending as-you-type search
let aborter = null;
let convId = null; // the overlay's Ask conversation, kept so follow-ups have context
let context = null; // {app, title, selection} from the window in front when summoned; sent with questions
let canInsert = false; // an answer can be pasted back (something was captured)

const MODES = ['search', 'ask', 'attach'];
const PLACEHOLDER = {
  search: 'Find a file — e.g. last month’s bill',
  ask: 'Ask a question about your files…',
  attach: 'What does the form need? e.g. resume…',
};

function setMode(next) {
  mode = next;
  tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.mode === mode)));
  input.placeholder = PLACEHOLDER[mode];
  keys.textContent = { search: '↑↓ choose · ↵ open · Tab switch · Esc close', ask: '↵ ask · Tab switch · Esc close', attach: 'Drag a file into the page · ↵ copy path · Esc close' }[mode];
}

// ATTACH: find the document a form field wants, then drag it from here into the page's upload box.
function attachRow(r) {
  const row = el('div', { class: 'result-row attach-row', draggable: r.sensitive ? null : 'true' });
  const sub = [fmtBytes(r.size), new Date(r.mtime * 1000).toLocaleDateString()].join(' · ');
  const tag = r.sensitive ? badge('Asks every time', 'red-outline') : '';
  const confirmBtn = r.sensitive
    ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: (e) => {
        e.stopPropagation();
        window.lfa.allowDrag([r.path], false);
        row.draggable = true;
        confirmBtn.remove();
        tag.textContent = 'Confirmed';
      } }, 'Attach anyway')
    : '';
  row.append(
    swatch(r.path),
    el('div', { class: 'result-body' },
      el('div', { class: 'result-line' }, el('span', { class: 'result-name' }, r.name), tag),
      el('div', { class: 'result-path' }, r.path),
      el('div', { class: 'result-snippet' }, r.snippet || sub)),
    confirmBtn,
  );
  row.addEventListener('dragstart', (e) => {
    e.preventDefault(); // Electron starts the real, native drag
    window.lfa.startDrag(r.path);
  });
  row.addEventListener('click', () => navigator.clipboard.writeText(r.path).then(() => (status.textContent = 'Path copied'), () => {}));
  return row;
}

async function runAttach() {
  timer = null;
  const q = input.value.trim();
  const mine = ++seq;
  if (!q) {
    window.lfa.allowDrag([]);
    body.replaceChildren();
    return;
  }
  try {
    const { results } = await api.formsSuggest({ label: q, pageTitle: context?.title });
    if (mine !== seq || mode !== 'attach') return;
    window.lfa.allowDrag(results.filter((r) => !r.sensitive).map((r) => r.path));
    status.textContent = `${results.length} file${results.length === 1 ? '' : 's'}`;
    body.replaceChildren(...(results.length ? results.map(attachRow) : [notice('No matching files. Try other words, like the type of document.')]));
    markOptions();
  } catch (e) {
    if (mine === seq) body.replaceChildren(notice(e.message, 'error'));
  }
}

function appName(exe) {
  return (exe || '').replace(/\.exe$/i, '').replace(/^./, (c) => c.toUpperCase());
}

function renderChip() {
  chipBox.hidden = !context;
  if (!context) return chipBox.replaceChildren();
  const from = [appName(context.app), context.title].filter(Boolean).join(' — ');
  chipBox.replaceChildren(
    el('div', { class: 'context-from' }, 'From: ', from),
    context.selection ? el('div', { class: 'context-sel' }, context.selection.slice(0, 200)) : '',
    el('button', { type: 'button', class: 'context-x', 'aria-label': 'Don’t use this context', onclick: () => { context = null; renderChip(); input.focus(); } }, '✕'),
  );
}

function reset(ctx) {
  aborter?.abort();
  setMood(lumi, 'idle');
  convId = null;
  context = ctx?.app ? ctx : null;
  canInsert = Boolean(context);
  renderChip();
  input.value = '';
  body.replaceChildren();
  markOptions();
  setMode(context?.selection ? 'ask' : 'search'); // text selected: the likely question is "about this"
  input.focus();
}

async function runSearch() {
  timer = null;
  const q = input.value.trim();
  const mine = ++seq;
  if (!q) {
    body.replaceChildren();
    return;
  }
  try {
    // Find: describe the file, get it back from names, dates and types on the PC (no stored text needed).
    const data = await api.find(q);
    if (mine !== seq || mode !== 'search') return;
    status.textContent = `${data.results.length} result${data.results.length === 1 ? '' : 's'} · ${(data.ms / 1000).toFixed(2)}s`;
    const terms = data.understood?.words || [];
    const rows = data.results.map((r) => ({ ...r, loc_kind: r.loc?.[0], loc_no: r.loc?.[1], snippet: r.snippet || `${r.folder} · ${r.modified}` }));
    body.replaceChildren(
      ...(rows.length
        ? rows.slice(0, document.body.classList.contains('overlay-compact') ? 4 : undefined).map((r) => resultRow(r, terms, (row) => api.findOpen(row.path, input.value.trim()).then(() => window.lfa.hideOverlay()).catch(() => {})))
        : [notice(data.pc_access ? 'No matching files. Try a word from the file name.' : 'Turn on PC access in Settings → Files & PC access so I can see your files.')]),
    );
    markOptions();
  } catch (e) {
    if (mine === seq) body.replaceChildren(notice(e.message, 'error'));
  }
}

function addActions(turn) {
  if (!turn.answer || turn.error || turn.noResults) return;
  const note = el('span', { class: 'action-status', role: 'status' });
  const copy = el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => navigator.clipboard.writeText(turn.answer).then(() => (note.textContent = 'Copied'), () => (note.textContent = 'Copy failed')) }, 'Copy');
  const insert = canInsert
    ? el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: async () => {
        if (!(await window.lfa.insertText(turn.answer))) note.textContent = 'Couldn’t insert — copied to clipboard';
      } }, 'Insert')
    : '';
  turn.box.append(el('div', { class: 'answer-actions' }, insert, copy, note));
}

async function runAsk() {
  const message = input.value.trim();
  if (!message) return;
  aborter?.abort();
  aborter = new AbortController();
  input.value = '';
  input.placeholder = 'Ask a follow-up…';
  status.textContent = 'Answering…';
  setMood(lumi, 'thinking');
  body.replaceChildren();
  markOptions();
  const turn = await ask({ log: body, message, conversationId: convId, mode: 'auto', context,  signal: aborter.signal, opts: { sourcesHeading: true } });
  if (turn.conversation) convId = turn.conversation.id;
  addActions(turn);
  if (readAloud.get() && turn.answer && !turn.error && !turn.noResults) speak(turn.answer).catch(() => {});
  status.textContent = 'Ready';
  setMood(lumi, 'idle');
}

// Results form a listbox driven from the input (combobox pattern), so screen readers announce
// the highlighted file as the arrow keys move, while focus stays in the text box.
function select(rows, next) {
  rows.forEach((r) => {
    r.classList.toggle('selected', r === next);
    r.setAttribute('aria-selected', String(r === next));
  });
  if (next) {
    input.setAttribute('aria-activedescendant', next.id);
    next.scrollIntoView({ block: 'nearest' });
  } else input.removeAttribute('aria-activedescendant');
}

function markOptions() {
  const rows = [...body.querySelectorAll('.result-row')];
  body.setAttribute('role', rows.length ? 'listbox' : 'region');
  input.setAttribute('aria-expanded', String(rows.length > 0));
  rows.forEach((r, i) => {
    r.id = `result-${i}`;
    r.setAttribute('role', 'option');
    r.tabIndex = -1;
  });
  select(rows, rows[0]);
}

function moveSelection(step) {
  const rows = [...body.querySelectorAll('.result-row')];
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.classList.contains('selected'));
  select(rows, rows[Math.max(0, Math.min(rows.length - 1, i + step))]);
}

input.addEventListener('input', () => {
  if (mode === 'ask') return;
  clearTimeout(timer);
  timer = setTimeout(mode === 'attach' ? runAttach : runSearch, 200);
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    window.lfa.hideOverlay();
  } else if (e.key === 'Tab') {
    e.preventDefault();
    clearTimeout(timer);
    timer = null;
    setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);
    body.replaceChildren();
    if (input.value.trim()) ({ search: runSearch, ask: runAsk, attach: runAttach })[mode]();
  } else if (e.key === 'ArrowDown' && mode !== 'ask') {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === 'ArrowUp' && mode !== 'ask') {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === 'Enter') {
    if (mode === 'ask') {
      runAsk();
      return;
    }
    const selected = body.querySelector('.result-row.selected');
    if (selected && timer === null) selected.click(); // results are current: open the file (ATTACH: copy its path)
    else {
      clearTimeout(timer);
      (mode === 'attach' ? runAttach : runSearch)();
    }
  }
});

tabs.forEach((t) =>
  t.addEventListener('click', () => {
    if (t.dataset.mode === 'organize') {
      window.lfa.openMain('organize');
      return;
    }
    setMode(t.dataset.mode);
    body.replaceChildren();
    input.focus();
  }),
);

// Voice: the mic button, or the voice shortcut (which opens the panel and starts listening).
const mic = micButton({
  onState: (s) => {
    input.placeholder = s === 'recording' ? 'Listening… just pause when you’re done' : s === 'working' ? 'Turning speech into text…' : PLACEHOLDER[mode];
    panel.classList.toggle('is-listening', s === 'recording');
  },
  onText: (text) => {
    setMode('ask');
    input.value = text;
    runAsk();
  },
  onError: (message) => body.replaceChildren(notice(message, 'error')),
});
mic.button.classList.add('ov-mic');
pin.before(mic.button);
window.lfa.onVoiceToggle(() => {
  setMode('ask');
  mic.toggle();
});

pin.addEventListener('click', () => {
  const on = pin.getAttribute('aria-pressed') !== 'true';
  pin.setAttribute('aria-pressed', String(on));
  window.lfa.pinOverlay(on);
});

window.lfa.onOverlayShown((ctx) => {
  loadAppearance(); // a theme or option changed in Settings applies the next time the overlay opens
  reset(ctx);
});

waitForBackend({ onWaiting: () => (status.textContent = 'Starting…') }).then((up) => {
  if (up) loadAppearance();
  status.textContent = up ? 'Ready' : 'Backend not running';
});
// ---------- strip sizing ----------
// Collapsed: just the strip. With results, an answer or a captured selection, the window grows down
// to fit them (main caps the height; past that the body scrolls).
const MARGIN = 24; // the see-through gap around the card (12px each side)
let fitQueued = false;
function fit() {
  if (fitQueued) return;
  fitQueued = true;
  requestAnimationFrame(() => {
    fitQueued = false;
    const open = body.childElementCount > 0 || !chipBox.hidden;
    panel.classList.toggle('is-open', open);
    const wanted = strip.offsetHeight + (open ? (chipBox.hidden ? 0 : chipBox.offsetHeight + 8) + body.scrollHeight + foot.offsetHeight + 2 : 0) + MARGIN;
    window.lfa.resizeOverlay?.(wanted);
  });
}
new MutationObserver(fit).observe(body, { childList: true, subtree: true, characterData: true });
new MutationObserver(fit).observe(chipBox, { attributes: true, childList: true });

setMode('search');
fit();
input.focus();
