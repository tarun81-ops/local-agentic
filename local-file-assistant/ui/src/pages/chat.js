import { api } from '../api.js';
import { ask, turnView, turnsFromMessages } from '../components/chat-log.js';
import { el, fileName, notice, svg, ICONS } from '../components/common.js';
import { micButton, readAloud, speak, stopSpeaking } from '../components/mic.js';

const STYLES = [
  ['', 'DEFAULT'],
  ['concise', 'CONCISE'],
  ['detailed', 'DETAILED'],
  ['study', 'STUDY'],
  ['simple', 'SIMPLE'],
];
const LANGS = [
  ['', 'DEFAULT'],
  ['english', 'ENGLISH'],
  ['hinglish', 'HINGLISH'],
];

const MODES = [
  ['auto', 'AUTO'],
  ['files', 'FILES ONLY'],
  ['chat', 'CHAT ONLY'],
];

/**
 * @param {HTMLElement} container
 * @param {{chatId?:number, onChatsChanged:()=>void}} opts
 */
export function render(container, { chatId, onChatsChanged }) {
  let convId = chatId ?? null; // set by the first reply of a new chat
  let busy = null; // AbortController while an answer streams
  let turns = 0;

  const scope = el('select', { id: 'ask-scope', class: 'scope-select', 'aria-label': 'Which files to search' }, el('option', { value: '' }, 'SEARCHING: ALL INDEXED FILES'));
  const mode = el('select', { id: 'chat-mode', class: 'scope-select', 'aria-label': 'Where answers come from' }, MODES.map(([v, t]) => el('option', { value: v }, `MODE: ${t}`)));
  const style = el('select', { id: 'chat-style', class: 'scope-select', 'aria-label': 'Answer style for the next questions (saved default in Settings)' }, STYLES.map(([v, t]) => el('option', { value: v }, `STYLE: ${t}`)));
  const lang = el('select', { id: 'chat-lang', class: 'scope-select', 'aria-label': 'Answer language for the next questions (saved default in Settings)' }, LANGS.map(([v, t]) => el('option', { value: v }, `LANGUAGE: ${t}`)));
  const log = el('div', { class: 'chat-log', id: 'chat-log', 'aria-live': 'polite' });
  const input = el('input', { id: 'ask-input', type: 'text', placeholder: 'Ask about your files, or anything else…', autocomplete: 'off' });
  const send = el('button', { type: 'button', class: 'send', 'aria-label': 'Send' }, svg(ICONS.send, 15));
  const mic = micButton({
    onText: (text) => {
      input.value = text;
      submit();
    },
    onError: (message) => log.append(notice(message, 'error')),
  });
  const aloud = el('input', { type: 'checkbox', id: 'read-aloud', class: 'toggle', role: 'switch' });
  aloud.checked = readAloud.get();
  aloud.addEventListener('change', () => {
    readAloud.set(aloud.checked);
    if (!aloud.checked) stopSpeaking();
  });

  container.replaceChildren(
    el(
      'header',
      { class: 'page-head' },
      el('h1', { class: 'page-title' }, 'CHAT'),
      el('label', { class: 'scope' }, mode, svg(ICONS.chevron, 11)),
      el('label', { class: 'scope' }, scope, svg(ICONS.chevron, 11)),
      el('label', { class: 'scope' }, style, svg(ICONS.chevron, 11)),
      el('label', { class: 'scope' }, lang, svg(ICONS.chevron, 11)),
      el('label', { class: 'aloud', for: 'read-aloud' }, aloud, 'READ ANSWERS ALOUD'),
    ),
    log,
    el(
      'div',
      { class: 'composer-wrap' },
      el('div', { class: 'composer' }, input, mic.button, send),
      el('div', { class: 'fineprint' }, 'FILE ANSWERS ARE GROUNDED IN YOUR FILES AND VERIFIED. GENERAL ANSWERS ARE NOT CHECKED.'),
    ),
  );

  api.roots().then(({ roots }) => {
    for (const r of roots) scope.append(el('option', { value: r.path }, `SEARCHING: ${(fileName(r.path) || r.path).toUpperCase()}`));
  }).catch(() => {});

  function emptyState() {
    log.replaceChildren(
      el(
        'div',
        { class: 'empty' },
        el('div', { class: 'empty-h' }, 'ASK ABOUT YOUR FILES, OR ANYTHING ELSE'),
        el('div', { class: 'empty-b' }, 'Answers from your files cite the exact file and page. Try “What is the invoice total for Acme Corp?”'),
      ),
    );
    api.recentFiles().then(({ files }) => {
      if (!files.length || turns) return;
      log.querySelector('.empty')?.append(
        el('div', { class: 'recent-h' }, 'RECENT AND RELEVANT'),
        el('div', { class: 'recent-files' }, files.map((f) => el('button', { type: 'button', class: 'btn btn-outline btn-sm', title: f.path, onclick: () => api.open(f.path).catch((e) => log.append(notice(e.message, 'error'))) }, `${f.name} · ${f.reason === 'opened' ? 'OPENED' : 'CHANGED'}`))),
      );
    }, () => {});
  }

  async function submit() {
    const message = input.value.trim();
    if (!message || busy) return;
    input.value = '';
    if (!turns) log.replaceChildren();
    busy = new AbortController();
    send.disabled = true;
    const turn = await ask({ log, message, conversationId: convId, mode: mode.value, root: scope.value, style: style.value, language: lang.value, signal: busy.signal, onScroll: () => (log.scrollTop = log.scrollHeight) });
    busy = null;
    send.disabled = false;
    turns++;
    if (aloud.checked && turn.answer && !turn.error && !turn.noResults) speak(turn.answer).catch((e) => log.append(notice(e.message, 'error')));
    if (turn.conversation) {
      convId = turn.conversation.id;
      onChatsChanged?.();
    }
    input.focus();
  }

  send.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });

  emptyState();
  if (convId != null) {
    api.conversation(convId).then((c) => {
      const old = turnsFromMessages(c.messages);
      if (!old.length || turns) return;
      turns = old.length;
      log.replaceChildren(...old.flatMap((t) => turnView(t)));
      log.scrollTop = log.scrollHeight;
    }).catch(() => {});
  }
  input.focus();
  return () => {
    busy?.abort();
    stopSpeaking();
  };
}
