// Renders a cited answer (numbered red badges in the text) and its source cards. Used by the
// Ask page and the overlay's Ask tab.
import { api } from '../api.js';
import { openRemind } from './remind.js';
import { openStudy } from './study.js';
import { el, fileName, highlight, locWords, svg, ICONS, swatch } from './common.js';

// "checked": the claim's facts are in the cited text. "unchecked": nothing specific to check.
// "failed": not found there. Chats saved before statuses existed only have `verified`.
const statusOf = (c) => c.status || (c.verified ? 'checked' : 'failed');
const CITE_CLASS = { checked: '', unchecked: 'cite-unchecked', failed: 'cite-unverified' };
const CITE_NOTE = { checked: '', unchecked: ' (cited, but nothing specific to check)', failed: ' (not verified)' };

function statusChip(c) {
  const status = statusOf(c);
  if (status === 'checked') return el('span', { class: 'src-status src-ok', title: 'The cited text contains this answer’s facts' }, svg(ICONS.check, 9), 'Verified');
  if (status === 'unchecked') return el('span', { class: 'src-status', title: 'Cited, but there was nothing specific to check' }, 'Cited');
  return el('span', { class: 'src-status src-bad', title: c.path ? 'The cited text doesn’t contain this answer’s facts' : 'This file wasn’t one the assistant read' }, c.path ? 'Not verified' : 'Not found');
}

/** The answer text with each "(file, page N)" citation replaced by its number badge, and
 * sentences that state facts without citing anything marked as unsourced. */
export function answerText(answer, citations, uncited) {
  const node = el('div', { class: 'answer-text' });
  const marks = [
    ...(citations || []).map((c) => ({ start: c.span[0], end: c.span[1], c })),
    ...(uncited || []).map(([start, end]) => ({ start, end })),
  ].sort((a, b) => a.start - b.start);
  let last = 0;
  for (const { start, end, c } of marks) {
    if (start < last) continue;
    const before = answer.slice(last, start);
    if (!c) {
      node.append(before, el('span', { class: 'uncited', title: 'No source cited for this sentence' }, answer.slice(start, end)));
    } else {
      const status = statusOf(c);
      node.append(
        before.replace(/\s+$/, ''),
        el('span', { class: `cite ${CITE_CLASS[status]}`, title: `${c.file}, ${locWords(c.loc_kind, c.loc_no)}${CITE_NOTE[status]}` }, String(c.n)),
      );
    }
    last = end;
  }
  node.append(answer.slice(last));
  return node;
}

/** A one-line note under the answer when parts of it are unsourced or it was cut off, else null. */
export function answerNote({ uncited, truncated }) {
  const parts = [];
  if (uncited?.length) parts.push('Underlined parts cite no file — check them yourself.');
  if (truncated) parts.push('The answer hit its length limit and was cut off.');
  return parts.length ? el('div', { class: 'answer-note', role: 'note' }, parts.join(' ')) : null;
}

// The numbers and names in the answer: the part of a source worth showing is the line that has them.
function answerTerms(answer) {
  const text = String(answer || '').replace(/[([][^()[\]]*\.\w{2,5}\s*,[^()[\]]*[)\]]/g, ' ');
  const nums = text.match(/\d[\d,./-]*\d|\d/g) || [];
  const names = (text.match(/\b[A-Z][A-Za-z]{2,}\b/g) || []).filter((w) => !/^(The|This|That|Your|You|Yes|And|But|For|From|With|Here|There|According)$/.test(w));
  return [...new Set([...nums, ...names])];
}

/** The line of the cited text that best backs the answer, trimmed to about one line, plus the terms it contains. */
export function bestExcerpt(snippet, answer, max = 150) {
  const text = String(snippet || '').replace(/\s+/g, ' ').trim();
  const terms = answerTerms(answer).filter((t) => text.toLowerCase().includes(t.toLowerCase()));
  if (text.length <= max) return { line: text, terms, cut: false };
  // Centre on the first number from the answer (else the first name), with its label before it ("Roll No. 12127198").
  const low = text.toLowerCase();
  const at = (t) => low.indexOf(t.toLowerCase());
  const anchor = terms.filter((t) => /\d/.test(t)).map(at).find((i) => i >= 0) ?? terms.map(at).find((i) => i >= 0) ?? 0;
  let from = Math.max(0, Math.min(anchor - Math.floor(max * 0.4), text.length - max));
  let to = Math.min(text.length, from + max);
  if (from > 0) from = text.indexOf(' ', from) + 1 || from; // whole words only
  if (to < text.length) to = text.lastIndexOf(' ', to) > from ? text.lastIndexOf(' ', to) : to;
  return { line: (from > 0 ? '…' : '') + text.slice(from, to).trim() + (to < text.length ? '…' : ''), terms, cut: true };
}

function moreMenu(c) {
  const name = fileName(c.file);
  const item = (label, fn) => el('button', { type: 'button', role: 'menuitem', class: 'menu-item', onclick: (e) => { e.target.closest('details').open = false; fn(); } }, label);
  const copied = el('span', { class: 'action-status', role: 'status' });
  return el(
    'details',
    { class: 'more-menu' },
    el('summary', { class: 'icon-btn', 'aria-label': `More for ${name}`, title: 'More' }, '⋯'),
    el(
      'div',
      { class: 'menu', role: 'menu' },
      item('Remind me about it', () => openRemind(c.path, name)),
      item('Study it (flashcards / quiz)', () => openStudy(c.path, name)),
      item('Copy file path', () => navigator.clipboard.writeText(c.path).then(() => { copied.textContent = 'Copied'; setTimeout(() => (copied.textContent = ''), 1400); }, () => {})),
    ),
    copied,
  );
}

function sourceRow(c, answer) {
  const name = fileName(c.file);
  const { line, terms, cut } = bestExcerpt(c.snippet, answer);
  const quote = el('div', { class: 'src-quote' });
  const showShort = () => {
    quote.replaceChildren(highlight(line, terms));
    if (cut) quote.append(' ', el('button', { type: 'button', class: 'link-btn', 'aria-expanded': 'false', onclick: showFull }, 'More'));
  };
  const showFull = () => quote.replaceChildren(highlight(String(c.snippet).trim(), terms), ' ', el('button', { type: 'button', class: 'link-btn', 'aria-expanded': 'true', onclick: showShort }, 'Less'));
  if (line) showShort();
  const open = () => api.open(c.path).catch(() => {});
  return el(
    'div',
    { class: 'src-row', role: 'listitem' },
    el(
      'div',
      { class: 'src-head' },
      el('span', { class: 'cite cite-static' }, String(c.n)),
      swatch(c.path || c.file, 'sm'),
      c.path
        ? el('button', { type: 'button', class: 'src-name', title: `Open ${c.path}`, onclick: open }, name)
        : el('span', { class: 'src-name', title: c.file }, name),
      el('span', { class: 'src-loc' }, locWords(c.loc_kind, c.loc_no)),
      statusChip(c),
      c.path
        ? el(
            'div',
            { class: 'src-actions' },
            el('button', { type: 'button', class: 'btn btn-ink btn-xs', 'aria-label': `Open ${name}`, onclick: open }, 'Open'),
            el('button', { type: 'button', class: 'btn btn-outline btn-xs', 'aria-label': `Show ${name} in its folder`, title: c.path, onclick: () => api.findReveal(c.path).catch(() => {}) }, 'Show in folder'),
            moreMenu(c),
          )
        : '',
    ),
    line ? quote : '',
  );
}

/** One compact row per cited file: name, page, check result and actions on one line, then the line of
 *  the file that backs the answer, with its numbers highlighted. A file cited twice shows its weakest check. */
export function sourceCards(citations, answer = '') {
  const rank = { failed: 0, unchecked: 1, checked: 2 };
  const seen = new Map();
  for (const c of citations || []) {
    const prev = seen.get(c.n);
    if (!prev || rank[statusOf(c)] < rank[statusOf(prev)]) seen.set(c.n, c);
  }
  if (!seen.size) return null;
  return el('div', { class: 'sources', role: 'list', 'aria-label': 'Sources' }, [...seen.values()].map((c) => sourceRow(c, answer)));
}

/** Files that matched the question, shown while the model is still writing. */
export function matchedFiles(results) {
  if (!results?.length) return null;
  const names = [...new Set(results.map((r) => r.name))];
  return el('div', { class: 'matched' }, `Reading ${names.length} file${names.length > 1 ? 's' : ''}: `, names.join(', '));
}
