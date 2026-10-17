import { api } from '../api.js';
import { badge, el, fmtBytes, highlight, locLabel, notice, swatch } from '../components/common.js';

// "Find that file": describe a file in your own words, get it back. Runs on rules + the index,
// no chat model, so it answers in about a second even on CPU.
const EXAMPLES = ['the electricity bill from last month', 'my internship offer letter', 'latest resume', 'class 10 marksheet pdf', 'blackmagic folder'];

function understoodLine(u) {
  const parts = [];
  if (u.words?.length) parts.push(`Words: ${u.words.join(', ')}`);
  if (u.type_label) parts.push(`Type: ${u.type_label.toLowerCase()}`);
  if (u.date_label) parts.push(`When: ${u.date_label}`);
  if (u.recent) parts.push('Newest first');
  return parts.join(' · ');
}

export function render(container, { navigate, prefill } = {}) {
  const input = el('input', { id: 'find-input', type: 'search', placeholder: 'Describe the file… e.g. the electricity bill from last month', autocomplete: 'off', 'aria-label': 'Describe the file you are looking for' });
  const meta = el('div', { class: 'page-meta', role: 'status', 'aria-live': 'polite' });
  const understood = el('div', { class: 'find-understood' });
  const results = el('div', { class: 'results', role: 'list' });
  const similar = el('div', { class: 'results find-similar', 'aria-live': 'polite' });
  const examples = el(
    'div',
    { class: 'find-examples' },
    el('span', { class: 'toolbar-label' }, 'Try'),
    ...EXAMPLES.map((t) => el('button', { type: 'button', class: 'chip chip-btn', onclick: () => { input.value = t; run(); } }, t)),
  );
  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'Find that file'), el('div', { class: 'page-sub' }, 'Describe it in your own words')), meta),
    el('div', { class: 'page-body' }, el('div', { class: 'search-bar' }, input), examples, understood, results, similar),
  );

  let seq = 0;
  let timer = null;
  const fail = (e) => results.prepend(notice(e.message, 'error'));
  const open = (r) => api.findOpen(r.path, input.value.trim()).catch(fail);
  const reveal = (r) => api.findReveal(r.path).catch(fail);

  function row(r, terms) {
    const loc = r.loc && r.loc[0] ? badge(locLabel(r.loc[0], r.loc[1])) : '';
    return el(
      'div',
      { class: 'result-wrap find-row', role: 'listitem' },
      el(
        'button',
        { type: 'button', class: 'result-row', onclick: () => open(r), title: r.is_folder ? 'Open this folder in File Explorer' : 'Open this file' },
        swatch(r.path, 'md', r.is_folder),
        el(
          'div',
          { class: 'result-body' },
          el('div', { class: 'result-line' }, el('span', { class: 'result-name' }, r.name), loc),
          el('div', { class: 'result-path' }, r.folder),
          el('div', { class: 'find-why' }, ...r.why.map((w) => badge(w)), el('span', { class: 'find-facts' }, r.is_folder ? `Folder · ${r.modified}` : `${r.modified} · ${fmtBytes(r.size)}`)),
          r.snippet ? el('div', { class: 'result-snippet' }, highlight(r.snippet, terms)) : '',
        ),
      ),
      el(
        'div',
        { class: 'result-study' },
        el('button', { type: 'button', class: 'btn btn-ink btn-sm', 'aria-label': `Open ${r.name}`, onclick: () => open(r) }, r.is_folder ? 'Open folder' : 'Open'),
        el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Show ${r.name} in its folder`, onclick: () => reveal(r) }, 'Show in folder'),
        r.is_folder ? '' : el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Files similar to ${r.name}`, onclick: () => showSimilar(r) }, 'Similar'),
      ),
    );
  }

  function similarBlock(title, list) {
    if (!list?.length) return [];
    return [el('div', { class: 'mem-group' }, title), ...list.map((r) => row(r, []))];
  }

  async function showSimilar(r) {
    similar.replaceChildren(el('div', { class: 'page-meta' }, `Looking for files like ${r.name}…`));
    try {
      const data = await api.findSimilar(r.path, 8);
      similar.replaceChildren(...(data.results.length ? similarBlock(`Similar to ${r.name}`, data.results) : [notice(`Nothing similar to ${r.name} found.`)]));
      similar.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      similar.replaceChildren(notice(e.message, 'error'));
    }
  }

  async function run() {
    const q = input.value.trim();
    const mine = ++seq;
    examples.hidden = !!q;
    similar.replaceChildren();
    if (!q) {
      results.replaceChildren();
      understood.replaceChildren();
      meta.textContent = '';
      return;
    }
    meta.textContent = 'Looking…';
    try {
      const data = await api.find(q);
      if (mine !== seq) return;
      const n = data.results.length;
      const kind = data.results.every((r) => r.is_folder) && n ? 'folder' : data.results.some((r) => r.is_folder) ? 'result' : 'file';
      meta.textContent = `${n} ${kind}${n === 1 ? '' : 'S'} · ${(data.ms / 1000).toFixed(2)}S`;
      const notes = [];
      if (data.relaxed?.includes('date')) notes.push('Nothing matched that date, so files from any date are shown.');
      if (data.relaxed?.includes('type')) notes.push(data.understood.type_label === 'FOLDER' ? 'No folder matched, so files are shown.' : 'Nothing matched that file type, so other types are shown.');
      understood.replaceChildren(
        el('div', { class: 'page-meta' }, understoodLine(data.understood)),
        ...notes.map((t) => notice(t)),
      );
      const terms = data.understood.words || [];
      if (n) {
        results.replaceChildren(...data.results.map((r) => row(r, terms)));
        similar.replaceChildren(...similarBlock('You may also want', data.similar));
      } else {
        results.replaceChildren(
          el(
            'div',
            { class: 'empty' },
            el('div', { class: 'empty-h' }, 'Nothing found'),
            el('div', { class: 'empty-b' }, data.pc_access
              ? 'Try other words: a word from the file name, or something written inside it.'
              : 'Only your indexed folders were searched. Turn on whole-PC access in Settings → Files & PC access to search every file name on this PC.'),
            data.pc_access ? '' : el('button', { type: 'button', class: 'btn btn-primary', onclick: () => navigate?.('index') }, 'Open files & PC access'),
          ),
        );
      }
    } catch (e) {
      if (mine === seq) {
        meta.textContent = '';
        results.replaceChildren(notice(e.message, 'error'));
      }
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(run, 350);
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
    if (i < 0) return;
    if (e.key === 'ArrowDown' && i < rows.length - 1) rows[i + 1].focus();
    if (e.key === 'ArrowUp') (i > 0 ? rows[i - 1] : input).focus();
  });
  input.focus();
  if (prefill?.q) {
    input.value = prefill.q; // sent here from chat's /find
    run();
  }
  return () => clearTimeout(timer);
}
