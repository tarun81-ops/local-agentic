import '@fontsource/archivo/400.css';
import '@fontsource/archivo/500.css';
import '@fontsource/archivo/700.css';
import '@fontsource/archivo/900.css';
import '@fontsource/space-mono/400.css';
import '@fontsource/space-mono/700.css';
import './style.css';

import { api, chat, waitForBackend } from './api.js';
import { answerNote, answerText, matchedFiles, sourceCards } from './components/answer.js';
import { el, notice } from './components/common.js';
import { resultRow } from './pages/search.js';

const scrim = document.getElementById('scrim');
const panel = document.getElementById('panel');
const input = document.getElementById('query');
const body = document.getElementById('overlay-body');
const footer = document.getElementById('overlay-footer');
const status = document.getElementById('overlay-status');
const tabs = [...document.querySelectorAll('.tab')];

let mode = 'search';
let seq = 0;
let timer = null; // pending as-you-type search
let aborter = null;

function setMode(next) {
  mode = next;
  tabs.forEach((t) => t.setAttribute('aria-selected', String(t.dataset.mode === mode)));
  input.placeholder = mode === 'ask' ? 'Ask a question about your files…' : 'Search your files, or press Tab to ask…';
}

function reset() {
  aborter?.abort();
  input.value = '';
  body.replaceChildren();
  markOptions();
  footer.hidden = true;
  setMode('search');
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
        ? data.results.map((r) => resultRow(r, data.terms, (row) => api.open(row.path).then(() => window.lfa.hideOverlay()).catch(() => {})))
        : [notice('No matches in your indexed files.')]),
    );
    markOptions();
    footer.hidden = false;
  } catch (e) {
    if (mine === seq) body.replaceChildren(notice(e.message, 'error'));
  }
}

async function runAsk() {
  const q = input.value.trim();
  if (!q) return;
  aborter?.abort();
  aborter = new AbortController();
  input.value = '';
  input.placeholder = 'Ask a follow-up…';
  footer.hidden = true;
  status.textContent = 'LOCAL — ANSWERING';
  const live = el('div', { class: 'answer-text streaming' });
  const progress = el('div', { class: 'matched' }, 'Searching your files…');
  const answerBox = el('div', { class: 'assistant' }, progress, live);
  body.replaceChildren(el('div', { class: 'bubble-user' }, q), answerBox);
  markOptions();
  let text = '';
  await chat(
    { question: q },
    {
      results: (d) => progress.replaceWith(matchedFiles(d.results) || progress),
      token: (d) => {
        text += d.delta;
        live.textContent = text;
      },
      done: (d) => {
        if (d.no_results) answerBox.replaceChildren(notice('Nothing in your indexed files matches that.'));
        else answerBox.replaceChildren(answerText(d.answer, d.citations, d.uncited), answerNote(d) || '', el('div', { class: 'sources-h' }, 'SOURCES'), sourceCards(d.citations) || '');
      },
      error: (d) => answerBox.replaceChildren(notice(d.detail, 'error')),
    },
    aborter.signal,
  );
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
  if (mode !== 'search') return;
  clearTimeout(timer);
  timer = setTimeout(runSearch, 200);
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault();
    window.lfa.hideOverlay();
  } else if (e.key === 'Tab') {
    e.preventDefault();
    clearTimeout(timer);
    timer = null;
    setMode(mode === 'search' ? 'ask' : 'search');
    if (mode === 'ask' && input.value.trim()) runAsk();
    else if (mode === 'search') runSearch();
  } else if (e.key === 'ArrowDown' && mode === 'search') {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === 'ArrowUp' && mode === 'search') {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === 'Enter') {
    if (mode === 'ask') {
      runAsk();
      return;
    }
    const selected = body.querySelector('.result-row.selected');
    if (selected && timer === null) selected.click(); // results are current: open the file
    else {
      clearTimeout(timer);
      runSearch();
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

// Clicking the dimmed backdrop (not the panel) closes the overlay.
scrim.addEventListener('mousedown', (e) => {
  if (!panel.contains(e.target)) window.lfa.hideOverlay();
});

window.lfa.onOverlayShown(reset);

waitForBackend({ onWaiting: () => (status.textContent = 'STARTING…') }).then((up) => {
  status.textContent = up ? 'LOCAL — READY' : 'BACKEND NOT RUNNING';
});
setMode('search');
input.focus();
