import { api } from '../api.js';
import { el, fileName, locWords, notice } from './common.js';

/**
 * A study dialog for one indexed file: generate flashcards or quiz questions, flip through them,
 * mark the ones you know, and export to CSV (Anki imports it). Cards live only in this dialog;
 * nothing is stored unless you export.
 */
export function openStudy(path, name = fileName(path)) {
  const dialog = el('dialog', { class: 'study-dialog', 'aria-label': `Study ${name}` });
  const kind = el('select', { class: 'field field-mono', 'aria-label': 'Card type' }, el('option', { value: 'flashcards' }, 'FLASHCARDS'), el('option', { value: 'quiz' }, 'QUIZ'));
  const count = el('input', { type: 'number', min: 5, max: 15, value: 8, class: 'shortcut-input', 'aria-label': 'How many cards (5 to 15)' });
  const go = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'GENERATE');
  const exportBtn = el('button', { type: 'button', class: 'btn btn-outline btn-sm', hidden: true }, 'EXPORT CSV');
  const close = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'CLOSE');
  const stage = el('div', { class: 'study-stage', 'aria-live': 'polite' });
  let items = [];
  let known = new Set();
  let at = 0;
  let shown = false;

  dialog.append(
    el('div', { class: 'study-head' }, el('div', { class: 'section-h' }, el('h2', {}, `STUDY — ${name.toUpperCase()}`)), close),
    el('div', { class: 'row-gap' }, kind, count, go, exportBtn),
    stage,
  );

  const next = () => {
    for (let i = 1; i <= items.length; i++) {
      const j = (at + i) % items.length;
      if (!known.has(j)) return (at = j);
    }
    return null;
  };

  function draw() {
    if (!items.length) return;
    if (known.size === items.length) {
      stage.replaceChildren(
        el('div', { class: 'empty-h' }, 'ALL CARDS KNOWN'),
        el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => { known = new Set(); at = 0; shown = false; draw(); } }, 'START OVER'),
      );
      return;
    }
    const card = items[at];
    const quiz = kind.value === 'quiz';
    const src = card.loc_kind ? el('div', { class: 'quiet' }, `SOURCE: ${locWords(card.loc_kind, card.loc_no)}`) : '';
    const reveal = el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => { shown = !shown; draw(); } }, shown ? 'HIDE' : quiz ? 'REVEAL ANSWER' : 'FLIP');
    const markKnown = el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => { known.add(at); shown = false; next(); draw(); } }, 'I KNOW THIS');
    const skip = el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => { shown = false; next(); draw(); } }, 'NEXT');
    stage.replaceChildren(
      el('div', { class: 'quiet' }, `CARD ${at + 1} OF ${items.length} · ${known.size} KNOWN`),
      el('div', { class: 'study-card' }, el('div', { class: 'study-front' }, card.front), shown ? el('div', { class: 'study-back' }, card.back) : ''),
      src,
      el('div', { class: 'row-gap' }, reveal, markKnown, skip),
    );
    reveal.focus();
  }

  go.addEventListener('click', async () => {
    go.disabled = true;
    stage.replaceChildren(notice('Writing cards with the local model…'));
    try {
      const n = Math.min(15, Math.max(5, Number(count.value) || 8));
      const r = await api.studyGenerate(path, kind.value, n);
      items = r.items;
      known = new Set();
      at = 0;
      shown = false;
      exportBtn.hidden = !items.length;
      if (!items.length) return stage.replaceChildren(notice('The model didn’t produce usable cards. Try again.', 'error'));
      draw();
      if (r.note) stage.prepend(notice(r.note));
    } catch (e) {
      stage.replaceChildren(notice(e.message, 'error'));
    } finally {
      go.disabled = false;
    }
  });

  exportBtn.addEventListener('click', async () => {
    try {
      const text = await api.studyCsv(items);
      const a = el('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: `${name}-cards.csv` });
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      stage.prepend(notice(e.message, 'error'));
    }
  });

  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  go.focus();
}
