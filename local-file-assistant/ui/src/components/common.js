// Small DOM helpers shared by the pages and the overlay. Text always goes in as text nodes,
// never innerHTML, because it comes from the user's files.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'style') node.style.cssText = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false || c === '') continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function svg(markup, size = 14) {
  const span = document.createElement('span');
  span.className = 'icon';
  span.style.cssText = `width:${size}px;height:${size}px`;
  span.innerHTML = markup; // static icon markup only
  return span;
}

export const ICONS = {
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>',
};

const TYPES = {
  pdf: ['PDF', 'red'],
  docx: ['DOC', 'blue'],
  doc: ['DOC', 'blue'],
  xlsx: ['XLS', 'blue'],
  pptx: ['PPT', 'ink'],
  png: ['PNG', 'yellow'],
  jpg: ['JPG', 'yellow'],
  jpeg: ['JPG', 'yellow'],
  webp: ['IMG', 'yellow'],
  bmp: ['IMG', 'yellow'],
};

export function fileName(path) {
  return String(path).split(/[\\/]/).pop();
}

export function swatch(path, size = 'md') {
  const ext = fileName(path).split('.').pop().toLowerCase();
  const [label, tone] = TYPES[ext] || [ext.slice(0, 3).toUpperCase(), 'ink'];
  return el('div', { class: `swatch swatch-${size} tone-${tone}`, 'aria-hidden': 'true' }, label);
}

export function locLabel(kind, no) {
  return { page: `P. ${no}`, part: `PART ${no}`, slide: `SLIDE ${no}`, sheet: `SHEET ${no}`, image: 'IMAGE' }[kind] || `${kind} ${no}`;
}

export function locWords(kind, no) {
  return { page: `p. ${no}`, part: `part ${no}`, slide: `slide ${no}`, sheet: `sheet ${no}`, image: 'image text' }[kind] || `${kind} ${no}`;
}

export function relPath(path, root) {
  if (root && path.startsWith(root)) return path.slice(root.length).replace(/^[\\/]/, '');
  return path;
}

export function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${units[i]}`;
}

export function fmtDate(iso) {
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** Text with the query terms wrapped in <mark>. */
export function highlight(text, terms) {
  const frag = document.createDocumentFragment();
  const words = (terms || []).filter((t) => t.length > 1).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!words.length) {
    frag.append(text);
    return frag;
  }
  const re = new RegExp(`(${words.join('|')})`, 'gi');
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    frag.append(el('mark', {}, m[0]));
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

export function badge(text, tone = 'outline') {
  return el('span', { class: `badge badge-${tone}` }, text);
}

export function notice(message, tone = 'neutral') {
  return el('div', { class: `notice notice-${tone}`, role: tone === 'error' ? 'alert' : 'status' }, message);
}
