import { el } from './common.js';

export const NAV_ITEMS = [
  { id: 'chat', n: '01', label: 'CHAT' },
  { id: 'search', n: '02', label: 'SEARCH' },
  { id: 'organize', n: '03', label: 'ORGANIZE' },
  { id: 'tasks', n: '04', label: 'TASKS' },
  { id: 'memory', n: '05', label: 'MEMORY' },
  { id: 'index', n: '06', label: 'INDEX' },
  { id: 'settings', n: '07', label: 'SETTINGS' },
];

/**
 * @param {object} p
 * @param {string} p.active  current page id
 * @param {(id:string)=>void} p.onSelect
 * @param {()=>void} p.onNewChat
 * @param {Array<{id:string,title:string}>} p.recent  recent chats (shown on the Chat page)
 * @param {(id:string)=>void} p.onOpenChat
 * @param {{model?:string, connected?:boolean}} p.status
 */
const ROW_HEIGHT = 40;

export function renderSidebar(root, { active, onSelect, onNewChat, recent, onOpenChat, status }) {
  const activeIndex = Math.max(0, NAV_ITEMS.findIndex((item) => item.id === active));
  root.replaceChildren(
    el('div', { class: 'brand' }, el('div', { class: 'mark' }, el('div')), el('div', { class: 'brand-name' }, 'LOCAL FILE ASSISTANT')),
    el('button', { type: 'button', class: `new-chat ${active === 'chat' ? 'new-chat-primary' : ''}`, onclick: onNewChat }, '+ NEW CHAT'),
    el(
      'nav',
      { class: 'nav', 'aria-label': 'Main' },
      el('div', { class: 'nav-indicator', style: `top:${activeIndex * ROW_HEIGHT}px` }),
      NAV_ITEMS.map((item) =>
        el(
          'button',
          {
            type: 'button',
            class: `nav-item ${item.id === active ? 'active' : ''}`,
            'aria-current': item.id === active ? 'page' : null,
            onclick: () => onSelect(item.id),
          },
          el('span', { class: 'nav-n' }, item.n),
          el('span', { class: 'nav-label' }, item.label),
        ),
      ),
    ),
    active === 'chat' && recent.length
      ? el(
          'div',
          { class: 'recent' },
          el('div', { class: 'recent-h' }, 'RECENT'),
          recent.slice(0, 8).map((c) =>
            el('button', { type: 'button', class: 'recent-item', title: c.title, onclick: () => onOpenChat(c.id) }, c.title),
          ),
        )
      : '',
    el(
      'div',
      { class: `status-box ${status.connected ? '' : 'status-off'}`, role: 'status', 'aria-live': 'polite' },
      el('div', { class: 'status-dot' }),
      el(
        'div',
        { class: 'status-text', title: status.connected ? `Ollama — ${status.model}` : 'Ollama is not running' },
        status.connected ? (status.model || '').toUpperCase() : 'OLLAMA — NOT RUNNING',
      ),
      el('div', { class: 'tag-local' }, 'LOCAL'),
    ),
  );
}
