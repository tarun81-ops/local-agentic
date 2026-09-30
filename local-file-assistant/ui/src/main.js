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
import { loadChats, render as renderAsk } from './pages/ask.js';
import { render as renderIndex } from './pages/index.js';
import { render as renderOrganize } from './pages/organize.js';
import { render as renderSearch } from './pages/search.js';
import { render as renderSettings } from './pages/settings.js';

const PAGES = { ask: renderAsk, search: renderSearch, organize: renderOrganize, index: renderIndex, settings: renderSettings };

const sidebar = document.getElementById('sidebar');
const content = document.getElementById('content');
const state = { page: 'ask', chatId: null, status: { connected: false, model: '' }, cleanup: null, ready: false };

function drawSidebar() {
  renderSidebar(sidebar, {
    active: state.page,
    onSelect: (id) => show(id),
    onNewChat: () => show('ask', { chatId: null }),
    recent: loadChats(),
    onOpenChat: (id) => show('ask', { chatId: id }),
    status: state.status,
  });
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
  state.page = PAGES[page] ? page : 'ask';
  if (!state.ready) return; // boot() shows the requested page once the backend is up
  state.cleanup?.();
  if (state.page === 'ask') state.chatId = chatId ?? null;
  drawSidebar();
  content.dataset.page = state.page;
  state.cleanup =
    PAGES[state.page](content, {
      chatId: state.chatId,
      navigate: show,
      onChatsChanged: drawSidebar,
      onStatusChanged: refreshStatus,
    }) || null;
}

async function boot() {
  drawSidebar();
  content.replaceChildren(el('div', { class: 'boot' }, el('div', { class: 'mark mark-lg' }, el('div')), 'STARTING THE LOCAL BACKEND…'));
  const up = await waitForBackend();
  if (!up) {
    content.replaceChildren(notice('The local backend didn’t start. Run scripts\\setup.ps1 once, then restart the app.', 'error'));
    return;
  }
  state.ready = true;
  show(state.page);
  refreshStatus();
  setInterval(refreshStatus, 30000);
}

window.lfa.onNavigate((page) => show(page));
boot();
