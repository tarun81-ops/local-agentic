import { api } from '../api.js';
import { badge, el, highlight, locLabel, notice, swatch } from '../components/common.js';

const MATCH = { both: ['KEYWORD + SEMANTIC', 'blue'], keyword: ['KEYWORD', 'outline'], semantic: ['SEMANTIC', 'outline'] };

export function resultRow(row, terms, onOpen) {
  const [label, tone] = MATCH[row.match] || MATCH.keyword;
  return el(
    'button',
    { type: 'button', class: 'result-row', onclick: () => onOpen(row) },
    swatch(row.path),
    el(
      'div',
      { class: 'result-body' },
      el('div', { class: 'result-line' }, el('span', { class: 'result-name' }, row.name), badge(locLabel(row.loc_kind, row.loc_no)), badge(label, tone)),
      el('div', { class: 'result-path' }, row.path),
      el('div', { class: 'result-snippet' }, highlight(row.snippet || '', terms)),
    ),
  );
}

export function render(container) {
  const input = el('input', { id: 'search-input', type: 'search', placeholder: 'Search your indexed files…', autocomplete: 'off' });
  const meta = el('div', { class: 'page-meta' });
  const results = el('div', { class: 'results' });
  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'SEARCH'), el('div', { class: 'page-sub' }, 'KEYWORD + SEMANTIC, RANKED TOGETHER')), meta),
    el('div', { class: 'page-body' }, el('div', { class: 'search-bar' }, input), results),
  );

  let seq = 0;
  let timer = null;
  const open = (row) => api.open(row.path, input.value.trim()).catch((e) => results.prepend(notice(e.message, 'error')));

  async function run() {
    const q = input.value.trim();
    const mine = ++seq;
    if (!q) {
      results.replaceChildren();
      meta.textContent = '';
      return;
    }
    meta.textContent = 'SEARCHING…';
    try {
      const data = await api.search(q);
      if (mine !== seq) return;
      meta.textContent = `${data.results.length} RESULTS · ${(data.ms / 1000).toFixed(2)}S${data.semantic ? '' : ' · KEYWORD ONLY'}`;
      results.replaceChildren(
        ...(data.results.length ? data.results.map((r) => resultRow(r, data.terms, open)) : [notice('No matches. Check the folder is added on the Index page, or try other words.')]),
      );
    } catch (e) {
      if (mine === seq) {
        meta.textContent = '';
        results.replaceChildren(notice(e.message, 'error'));
      }
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(run, 250);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      clearTimeout(timer);
      run();
    }
    if (e.key === 'ArrowDown') results.querySelector('.result-row')?.focus();
  });
  results.addEventListener('keydown', (e) => {
    const rows = [...results.querySelectorAll('.result-row')];
    const i = rows.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' && i < rows.length - 1) rows[i + 1].focus();
    if (e.key === 'ArrowUp') (i > 0 ? rows[i - 1] : input).focus();
  });
  input.focus();
}
