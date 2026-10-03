import '@fontsource/archivo/400.css';
import '@fontsource/archivo/500.css';
import '@fontsource/archivo/700.css';
import '@fontsource/archivo/900.css';
import '@fontsource/space-mono/400.css';
import '@fontsource/space-mono/700.css';
import './style.css';

import { api, waitForBackend } from './api.js';
import { el, notice } from './components/common.js';
import { renderSidebar } from './components/sidebar.js';
import { render as renderChat } from './pages/chat.js';
import { render as renderIndex } from './pages/index.js';
import { render as renderMemory } from './pages/memory.js';
import { render as renderOrganize } from './pages/organize.js';
import { render as renderSearch } from './pages/search.js';
import { render as renderSettings } from './pages/settings.js';
import { render as renderTasks } from './pages/tasks.js';

const PAGES = { chat: renderChat, search: renderSearch, organize: renderOrganize, tasks: renderTasks, memory: renderMemory, index: renderIndex, settings: renderSettings };

const sidebar = document.getElementById('sidebar');
const content = document.getElementById('content');
const state = { page: 'chat', chatId: null, recent: [], status: { connected: false, model: '' }, cleanup: null, ready: false };

function drawSidebar() {
  renderSidebar(sidebar, {
    active: state.page,
    onSelect: (id) => show(id),
    onNewChat: () => show('chat', { chatId: null }),
    recent: state.recent,
    onOpenChat: (id) => show('chat', { chatId: id }),
    status: state.status,
  });
}

async function refreshRecent() {
  try {
    state.recent = (await api.conversations()).conversations;
  } catch { /* keep the list we have */ }
  drawSidebar();
}

// One-time move of the old browser-stored chats into the backend's conversation store.
async function importOldChats() {
  try {
    const old = JSON.parse(localStorage.getItem('lfa.chats') || '[]');
    if (old.length) await api.importConversations(old);
    localStorage.removeItem('lfa.chats');
  } catch { /* try again next launch */ }
}

async function refreshStatus() {
  try {
    const s = await api.settings();
    state.status = { connected: s.ollama.connected, model: s.model };
  } catch {
    state.status = { connected: false, model: '' };
  }
  drawSidebar();
}

function show(page, { chatId } = {}) {
  state.page = PAGES[page] ? page : 'chat';
  if (!state.ready) return; // boot() shows the requested page once the backend is up
  state.cleanup?.();
  if (state.page === 'chat') state.chatId = chatId ?? null;
  drawSidebar();
  content.dataset.page = state.page;
  state.cleanup =
    PAGES[state.page](content, {
      chatId: state.chatId,
      navigate: show,
      onChatsChanged: refreshRecent,
      onStatusChanged: refreshStatus,
    }) || null;
}

async function boot() {
  drawSidebar();
  content.replaceChildren(el('div', { class: 'boot' }, el('div', { class: 'mark mark-lg' }, el('div')), 'STARTING THE LOCAL BACKEND… THE FIRST START CAN TAKE A MINUTE'));
  const up = await waitForBackend();
  if (!up) {
    content.replaceChildren(notice('The local backend didn’t start. Run scripts\\setup.ps1 once, then restart the app.', 'error'));
    return;
  }
  await importOldChats();
  state.ready = true;
  show(state.page);
  refreshRecent();
  refreshStatus();
  setInterval(refreshStatus, 30000);
}

window.lfa.onNavigate((page) => show(page));
boot();
