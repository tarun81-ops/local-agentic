// Renders a cited answer (numbered red badges in the text) and its source cards. Used by the
// Ask page and the overlay's Ask tab.
import { api } from '../api.js';
import { openStudy } from './study.js';
import { badge, el, fileName, locWords, notice, svg, ICONS, swatch } from './common.js';

// "checked": the claim's facts are in the cited text. "unchecked": nothing specific to check.
// "failed": not found there. Chats saved before statuses existed only have `verified`.
const statusOf = (c) => c.status || (c.verified ? 'checked' : 'failed');
const CITE_CLASS = { checked: '', unchecked: 'cite-unchecked', failed: 'cite-unverified' };
const CITE_NOTE = { checked: '', unchecked: ' (cited, but nothing specific to check)', failed: ' (not verified)' };

function statusBadge(c) {
  const status = statusOf(c);
  if (status === 'checked') return el('span', { class: 'badge badge-blue' }, svg(ICONS.check, 9), ' VERIFIED');
  if (status === 'unchecked') return badge('CITED, NOT CHECKED', 'outline');
  return badge(c.path ? 'NOT VERIFIED' : 'NOT FOUND IN YOUR FILES', 'red-outline');
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

/** A warning under the answer when parts of it are unsourced or it was cut off, else null. */
export function answerNote({ uncited, truncated }) {
  const parts = [];
  if (uncited?.length) parts.push('Underlined sentences cite no source; check them against your files.');
  if (truncated) parts.push('The answer hit its length limit and was cut off.');
  return parts.length ? notice(parts.join(' ')) : null;
}

export function sourceCards(citations) {
  // One card per source; if it's cited more than once, the card shows the weakest result.
  const rank = { failed: 0, unchecked: 1, checked: 2 };
  const seen = new Map();
  for (const c of citations || []) {
    const prev = seen.get(c.n);
    if (!prev || rank[statusOf(c)] < rank[statusOf(prev)]) seen.set(c.n, c);
  }
  if (!seen.size) return null;
  return el(
    'div',
    { class: 'sources' },
    [...seen.values()].map((c) =>
      el(
        'div',
        { class: 'source-card' },
        el(
          'div',
          { class: 'source-head' },
          el('span', { class: 'cite cite-static' }, String(c.n)),
          swatch(c.path || c.file, 'sm'),
          el('div', { class: 'source-name' }, fileName(c.file)),
        ),
        statusBadge(c),
        c.snippet ? el('div', { class: 'source-quote' }, `“${c.snippet}”`) : null,
        el(
          'div',
          { class: 'source-foot' },
          el('div', { class: 'source-path' }, `${c.path || c.file} · ${locWords(c.loc_kind, c.loc_no)}`),
          c.path ? el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Study ${fileName(c.file)}`, onclick: () => openStudy(c.path, fileName(c.file)) }, 'STUDY') : null,
          c.path ? el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => api.open(c.path).catch(() => {}) }, 'OPEN') : null,
        ),
      ),
    ),
  );
}

/** Files that matched the question, shown while the model is still writing. */
export function matchedFiles(results) {
  if (!results?.length) return null;
  const names = [...new Set(results.map((r) => r.name))];
  return el('div', { class: 'matched' }, `Reading ${names.length} file${names.length > 1 ? 's' : ''}: `, names.join(', '));
}
