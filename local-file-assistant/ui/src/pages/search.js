import { api } from '../api.js';
import { openRemind } from '../components/remind.js';
import { openStudy } from '../components/study.js';
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

export function render(container, { prefill, onCollectionsChanged } = {}) {
  let root = null; // set when a saved search is limited to one folder
  const input = el('input', { id: 'search-input', type: 'search', placeholder: 'Search your indexed files…', autocomplete: 'off' });
  const meta = el('div', { class: 'page-meta' });
  const results = el('div', { class: 'results' });
  const saveBtn = el('button', { type: 'button', class: 'btn btn-outline btn-sm', hidden: true }, 'SAVE THIS SEARCH');
  const saveBox = el('div', { class: 'row-gap' }, saveBtn);
  const saved = el('div');
  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'SEARCH'), el('div', { class: 'page-sub' }, 'KEYWORD + SEMANTIC, RANKED TOGETHER')), meta),
    el('div', { class: 'page-body' }, el('div', { class: 'search-bar' }, input), saveBox, saved, results),
  );

  let seq = 0;
  let timer = null;
  const open = (row) => api.open(row.path, input.value.trim()).catch((e) => results.prepend(notice(e.message, 'error')));

  /** Saved searches: shown while the box is empty. RUN fills the box and searches. */
  async function loadSaved() {
    try {
      const { collections } = await api.collections();
      saved.replaceChildren(
        ...(collections.length
          ? [
              el('div', { class: 'mem-group' }, 'SAVED SEARCHES'),
              ...collections.map((c) =>
                el(
                  'div',
                  { class: 'mem-row' },
                  el('div', { class: 'mem-text' }, `${c.name} — “${c.query}”${c.root ? ` in ${c.root}` : ''}`),
                  el(
                    'div',
                    { class: 'mem-actions' },
                    el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => useCollection(c) }, 'RUN'),
                    el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-pressed': String(c.pinned), onclick: () => api.updateCollection(c.id, { pinned: !c.pinned }).then(() => { onCollectionsChanged?.(); loadSaved(); }) }, c.pinned ? 'PINNED' : 'PIN'),
                    el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Delete ${c.name}`, onclick: () => api.deleteCollection(c.id).then(() => { onCollectionsChanged?.(); loadSaved(); }) }, 'DELETE'),
                  ),
                ),
              ),
            ]
          : []),
      );
    } catch { saved.replaceChildren(); }
  }

  function useCollection(c) {
    root = c.root || null;
    input.value = c.query;
    run();
  }

  saveBtn.addEventListener('click', () => {
    const name = el('input', { type: 'text', class: 'field', maxlength: 60, value: input.value.trim().slice(0, 60), 'aria-label': 'Name for this search' });
    const ok = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SAVE');
    const cancel = el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => saveBox.replaceChildren(saveBtn) }, 'CANCEL');
    ok.addEventListener('click', () =>
      api.createCollection({ name: name.value, query: input.value.trim(), root }).then(
        () => { saveBox.replaceChildren(notice('Saved, and pinned in the sidebar.'), saveBtn); onCollectionsChanged?.(); },
        (e) => saveBox.replaceChildren(notice(e.message, 'error'), saveBtn),
      ),
    );
    saveBox.replaceChildren(name, ok, cancel);
    name.focus();
  });

  async function run() {
    const q = input.value.trim();
    const mine = ++seq;
    saveBtn.hidden = !q;
    if (!q) {
      results.replaceChildren();
      meta.textContent = '';
      root = null;
      loadSaved();
      return;
    }
    saved.replaceChildren();
    meta.textContent = 'SEARCHING…';
    try {
      const data = await api.search(q, root);
      if (mine !== seq) return;
      meta.textContent = `${data.results.length} RESULTS · ${(data.ms / 1000).toFixed(2)}S${data.semantic ? '' : ' · KEYWORD ONLY'}`;
      results.replaceChildren(
        ...(data.results.length ? data.results.map((r) => el('div', { class: 'result-wrap' }, resultRow(r, data.terms, open), el('div', { class: 'result-study' }, el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Remind me about ${r.name}`, onclick: () => openRemind(r.path, r.name) }, 'REMIND ME'), el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Study ${r.name}`, onclick: () => openStudy(r.path, r.name) }, 'STUDY')))) : [notice('No matches. Check the folder is added on the Index page, or try other words.')]),
      );
    } catch (e) {
      if (mine === seq) {
        meta.textContent = '';
        results.replaceChildren(notice(e.message, 'error'));
      }
    }
  }

  input.addEventListener('input', () => {
    root = null; // typing a new search leaves the saved folder limit behind
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
  if (prefill?.collectionId != null) {
    api.collections().then(({ collections }) => {
      const c = collections.find((x) => x.id === prefill.collectionId);
      if (c) useCollection(c);
    }, () => {});
  } else loadSaved();
}
