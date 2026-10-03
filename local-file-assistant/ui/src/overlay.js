import '@fontsource/archivo/400.css';
import '@fontsource/archivo/500.css';
import '@fontsource/archivo/700.css';
import '@fontsource/archivo/900.css';
import '@fontsource/space-mono/400.css';
import '@fontsource/space-mono/700.css';
import './style.css';

import { api, waitForBackend } from './api.js';
import { ask } from './components/chat-log.js';
import { micButton, readAloud, speak } from './components/mic.js';
import { badge, el, fmtBytes, notice, swatch } from './components/common.js';
import { resultRow } from './pages/search.js';

const input = document.getElementById('query');
const body = document.getElementById('overlay-body');
const footer = document.getElementById('overlay-footer');
const status = document.getElementById('overlay-status');
const tabHint = document.getElementById('tab-hint');
const pin = document.getElementById('pin');
const styleSel = document.getElementById('overlay-style');
const chipBox = document.getElementById('context-chip');
const tabs = [...document.querySelectorAll('.tab')];

let mode = 'search';
let seq = 0;
let timer = null; // pending as-you-type search
let aborter = null;
let convId = null; // the overlay's Ask conversation, kept so follow-ups have context
let context = null; // {app, title, selection} from the window in front when summoned; sent with questions
let canInsert = false; // an answer can be pasted back (something was captured)

const MODES = ['search', 'ask', 'attach'];
const PLACEHOLDER = {
  search: 'Search your files, or press Tab to ask…',
  ask: 'Ask a question about your files…',
  attach: 'What does the form ask for? e.g. resume, ID proof…',
};

function setMode(next) {
  mode = next;
  tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.mode === mode)));
  input.placeholder = PLACEHOLDER[mode];
  tabHint.textContent = `TAB → ${MODES[(MODES.indexOf(mode) + 1) % MODES.length].toUpperCase()}`;
  footer.firstElementChild.textContent = mode === 'attach' ? '↑↓ NAVIGATE   ↵ COPY PATH   DRAG A FILE INTO THE PAGE' : '↑↓ NAVIGATE   ↵ OPEN   TAB ASK AI';
}

// ATTACH: find the document a form field wants, then drag it from here into the page's upload box.
function attachRow(r) {
  const row = el('div', { class: 'result-row attach-row', draggable: r.sensitive ? null : 'true' });
  const sub = [fmtBytes(r.size), new Date(r.mtime * 1000).toLocaleDateString()].join(' · ');
  const tag = r.sensitive ? badge('ASKS EVERY TIME', 'red-outline') : '';
  const confirmBtn = r.sensitive
    ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: (e) => {
        e.stopPropagation();
        window.lfa.allowDrag([r.path], false);
        row.draggable = true;
        confirmBtn.remove();
        tag.textContent = 'CONFIRMED';
      } }, 'ATTACH ANYWAY')
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
  row.addEventListener('click', () => navigator.clipboard.writeText(r.path).then(() => (status.textContent = 'PATH COPIED'), () => {}));
  return row;
}

async function runAttach() {
  timer = null;
  const q = input.value.trim();
  const mine = ++seq;
  if (!q) {
    window.lfa.allowDrag([]);
    body.replaceChildren();
    footer.hidden = true;
    return;
  }
  try {
    const { results } = await api.formsSuggest({ label: q, pageTitle: context?.title });
    if (mine !== seq || mode !== 'attach') return;
    window.lfa.allowDrag(results.filter((r) => !r.sensitive).map((r) => r.path));
    status.textContent = `${results.length} FILES`;
    body.replaceChildren(...(results.length ? results.map(attachRow) : [notice('No matching files. Try other words, like the type of document.')]));
    markOptions();
    footer.hidden = false;
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
    el('div', { class: 'context-from' }, 'FROM: ', from),
    context.selection ? el('div', { class: 'context-sel' }, context.selection.slice(0, 200)) : '',
    el('button', { type: 'button', class: 'context-x', 'aria-label': 'Don’t use this context', onclick: () => { context = null; renderChip(); input.focus(); } }, '✕'),
  );
}

function reset(ctx) {
  aborter?.abort();
  convId = null;
  context = ctx?.app ? ctx : null;
  canInsert = Boolean(context);
  renderChip();
  input.value = '';
  body.replaceChildren();
  markOptions();
  footer.hidden = true;
  setMode(context?.selection ? 'ask' : 'search'); // text selected: the likely question is "about this"
  input.focus();
}

async function runSearch() {
  timer = null;
  const q = input.value.trim();
  const mine = ++seq;
  if (!q) {
    body.replaceChildren();
    footer.hidden = true;
    return;
  }
  try {
    const data = await api.search(q);
    if (mine !== seq || mode !== 'search') return;
    status.textContent = `${data.results.length} RESULTS · ${(data.ms / 1000).toFixed(2)}S`;
    body.replaceChildren(
      ...(data.results.length
        ? data.results.map((r) => resultRow(r, data.terms, (row) => api.open(row.path, input.value.trim()).then(() => window.lfa.hideOverlay()).catch(() => {})))
        : [notice('No matches in your indexed files.')]),
    );
    markOptions();
    footer.hidden = false;
  } catch (e) {
    if (mine === seq) body.replaceChildren(notice(e.message, 'error'));
  }
}

function addActions(turn) {
  if (!turn.answer || turn.error || turn.noResults) return;
  const note = el('span', { class: 'action-status', role: 'status' });
  const copy = el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => navigator.clipboard.writeText(turn.answer).then(() => (note.textContent = 'COPIED'), () => (note.textContent = 'COPY FAILED')) }, 'COPY');
  const insert = canInsert
    ? el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: async () => {
        if (!(await window.lfa.insertText(turn.answer))) note.textContent = 'COULDN’T INSERT — COPIED TO CLIPBOARD';
      } }, 'INSERT')
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
  footer.hidden = true;
  status.textContent = 'LOCAL — ANSWERING';
  body.replaceChildren();
  markOptions();
  const turn = await ask({ log: body, message, conversationId: convId, mode: 'auto', context, style: styleSel.value, signal: aborter.signal, opts: { sourcesHeading: true } });
  if (turn.conversation) convId = turn.conversation.id;
  addActions(turn);
  if (readAloud.get() && turn.answer && !turn.error && !turn.noResults) speak(turn.answer).catch(() => {});
  status.textContent = 'LOCAL — READY';
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
    footer.hidden = true;
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
    footer.hidden = true;
    input.focus();
  }),
);

// Voice: the mic button, or the voice shortcut (which opens the panel and starts listening).
const mic = micButton({
  onText: (text) => {
    setMode('ask');
    input.value = text;
    runAsk();
  },
  onError: (message) => body.replaceChildren(notice(message, 'error')),
});
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

window.lfa.onOverlayShown(reset);

waitForBackend({ onWaiting: () => (status.textContent = 'STARTING…') }).then((up) => {
  status.textContent = up ? 'LOCAL — READY' : 'BACKEND NOT RUNNING';
});
setMode('search');
input.focus();
