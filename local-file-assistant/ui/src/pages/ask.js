import { chat, api } from '../api.js';
import { answerNote, answerText, matchedFiles, sourceCards } from '../components/answer.js';
import { el, fileName, notice, svg, ICONS } from '../components/common.js';

const STORE_KEY = 'lfa.chats';
const MAX_CHATS = 20;

export function loadChats() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveChat(chatRecord) {
  const chats = loadChats().filter((c) => c.id !== chatRecord.id);
  chats.unshift(chatRecord);
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(chats.slice(0, MAX_CHATS)));
  } catch { /* storage full or blocked: the chat still works, it just isn't kept */ }
}

/**
 * @param {HTMLElement} container
 * @param {{chatId?:string, onChatsChanged:()=>void}} opts
 */
export function render(container, { chatId, onChatsChanged }) {
  const record = (chatId && loadChats().find((c) => c.id === chatId)) || { id: crypto.randomUUID(), title: '', at: Date.now(), turns: [] };
  let busy = null; // AbortController while an answer streams

  const scope = el('select', { id: 'ask-scope', class: 'scope-select', 'aria-label': 'Which files to search' }, el('option', { value: '' }, 'SEARCHING: ALL INDEXED FILES'));
  const log = el('div', { class: 'chat-log', id: 'chat-log', 'aria-live': 'polite' });
  const input = el('input', { id: 'ask-input', type: 'text', placeholder: 'Ask about your files…', autocomplete: 'off' });
  const send = el('button', { type: 'button', class: 'send', 'aria-label': 'Send' }, svg(ICONS.send, 15));

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('h1', { class: 'page-title' }, 'ASK'), el('label', { class: 'scope' }, scope, svg(ICONS.chevron, 11))),
    log,
    el(
      'div',
      { class: 'composer-wrap' },
      el('div', { class: 'composer' }, input, send),
      el('div', { class: 'fineprint' }, 'ANSWERS ARE GROUNDED IN YOUR FILES AND VERIFIED BEFORE THEY’RE SHOWN.'),
    ),
  );

  api.roots().then(({ roots }) => {
    for (const r of roots) scope.append(el('option', { value: r.path }, `SEARCHING: ${(fileName(r.path) || r.path).toUpperCase()}`));
  }).catch(() => {});

  function emptyState() {
    if (record.turns.length) return;
    log.replaceChildren(
      el(
        'div',
        { class: 'empty' },
        el('div', { class: 'empty-h' }, 'ASK ANYTHING ABOUT YOUR FILES'),
        el('div', { class: 'empty-b' }, 'Answers cite the exact file and page they came from. Try “What is the invoice total for Acme Corp?”'),
      ),
    );
  }

  function fillAnswer(box, turn) {
    if (turn.error) box.replaceChildren(notice(turn.error, 'error'));
    else if (turn.noResults) box.replaceChildren(notice('Nothing in your indexed files matches that. Add a folder on the Index page, or try different words.'));
    else box.replaceChildren(answerText(turn.answer, turn.citations, turn.uncited), answerNote(turn) || '', sourceCards(turn.citations) || '');
  }

  function turnView(turn) {
    const answerBox = el('div', { class: 'assistant' });
    if (turn.answer != null) fillAnswer(answerBox, turn);
    return [el('div', { class: 'bubble-user' }, turn.q), answerBox];
  }

  function scrollDown() {
    log.scrollTop = log.scrollHeight;
  }

  async function ask() {
    const q = input.value.trim();
    if (!q || busy) return;
    input.value = '';
    if (!record.turns.length) log.replaceChildren();
    const turn = { q, answer: null, citations: [] };
    const [bubble, box] = turnView(turn);
    const live = el('div', { class: 'answer-text streaming' });
    const status = el('div', { class: 'matched' }, 'Searching your files…');
    box.append(status, live);
    log.append(bubble, box);
    scrollDown();

    busy = new AbortController();
    send.disabled = true;
    let text = '';
    await chat(
      { question: q, root: scope.value },
      {
        results: (d) => status.replaceWith(matchedFiles(d.results) || status),
        warning: (d) => box.prepend(notice(d.detail)),
        token: (d) => {
          text += d.delta;
          live.textContent = text;
          scrollDown();
        },
        done: (d) => {
          Object.assign(turn, { answer: d.answer, citations: d.citations, uncited: d.uncited, truncated: d.truncated, noResults: d.no_results });
          fillAnswer(box, turn);
        },
        error: (d) => {
          turn.error = d.detail;
          turn.answer = '';
          fillAnswer(box, turn);
        },
      },
      busy.signal,
    );
    busy = null;
    send.disabled = false;
    if (turn.answer != null && !turn.error) {
      record.turns.push(turn);
      record.title ||= q.length > 60 ? `${q.slice(0, 57)}…` : q;
      record.at = Date.now();
      saveChat(record);
      onChatsChanged?.();
    }
    scrollDown();
    input.focus();
  }

  send.addEventListener('click', ask);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') ask(); });

  if (record.turns.length) log.replaceChildren(...record.turns.flatMap(turnView));
  emptyState();
  input.focus();
  return () => busy?.abort();
}
