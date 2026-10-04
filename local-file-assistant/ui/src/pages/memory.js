import { api } from '../api.js';
import { el, notice } from '../components/common.js';

/** Everything the assistant remembers about you: searchable, editable, pinnable, deletable. */
export function render(container) {
  const search = el('input', { type: 'text', class: 'field', placeholder: 'Search memories…', 'aria-label': 'Search memories' });
  const add = el('input', { type: 'text', class: 'field', placeholder: 'Add something to remember…', 'aria-label': 'New memory' });
  const addBtn = el('button', { type: 'button', class: 'btn btn-ink' }, 'ADD');
  const exportBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'EXPORT');
  const wipeBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'WIPE ALL');
  const flash = el('div');
  const summary = el('div', { class: 'page-sub', role: 'status' });
  const review = el('div', { class: 'page-body' });
  const list = el('div', { class: 'page-body' });

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'MEMORY'), el('div', { class: 'page-sub' }, 'WHAT THE ASSISTANT REMEMBERS — STORED ONLY ON THIS DEVICE')), el('div', { class: 'head-actions' }, exportBtn, wipeBtn)),
    el('div', { class: 'toolbar' }, search, add, addBtn),
    summary,
    flash,
    review,
    list,
  );

  const fail = (e) => flash.replaceChildren(notice(e.message, 'error'));

  function row(m) {
    const text = el('div', { class: 'mem-text' }, m.text);
    const pin = el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-pressed': String(m.pinned), onclick: () => api.updateMemory(m.id, { pinned: !m.pinned }).then(load, fail) }, m.pinned ? 'PINNED' : 'PIN');
    const edit = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'EDIT');
    const del = el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Delete: ${m.text}`, onclick: () => api.deleteMemory(m.id).then(load, fail) }, 'DELETE');
    edit.addEventListener('click', () => {
      const input = el('input', { type: 'text', class: 'field', value: m.text, 'aria-label': 'Edit memory' });
      const save = () => api.updateMemory(m.id, { text: input.value }).then(load, fail);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') load(); });
      text.replaceChildren(input);
      input.focus();
    });
    return el('div', { class: 'mem-row' }, text, el('div', { class: 'mem-actions' }, pin, edit, del));
  }

  function pendingRow(m) {
    const text = el('div', { class: 'mem-text' }, m.text);
    const approve = el('button', { type: 'button', class: 'btn btn-ink btn-sm', 'aria-label': `Approve: ${m.text}`, onclick: () => api.approveMemory(m.id).then(load, fail) }, 'APPROVE');
    const edit = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'EDIT');
    const dismiss = el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Dismiss: ${m.text}`, onclick: () => api.deleteMemory(m.id).then(load, fail) }, 'DISMISS');
    edit.addEventListener('click', () => {
      const input = el('input', { type: 'text', class: 'field', value: m.text, 'aria-label': 'Edit before approving' });
      const save = () => api.updateMemory(m.id, { text: input.value }).then(load, fail);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') load(); });
      text.replaceChildren(input);
      input.focus();
    });
    return el('div', { class: 'mem-row' }, text, el('div', { class: 'mem-actions' }, approve, edit, dismiss));
  }

  async function load() {
    flash.replaceChildren();
    try {
      const [{ memories, counts }, pending] = await Promise.all([api.memories(search.value.trim()), api.memories('', 'pending')]);
      summary.textContent = `WHAT I KNOW ABOUT YOU — ${counts.active} REMEMBERED, ${counts.pending} WAITING FOR YOUR REVIEW`;
      if (pending.memories.length) {
        const all = el('button', { type: 'button', class: 'btn btn-ink btn-sm', onclick: () => api.approveAllMemories().then(load, fail) }, 'APPROVE ALL');
        review.replaceChildren(
          el('div', { class: 'mem-group' }, 'NEEDS REVIEW — NOT USED IN ANSWERS UNTIL YOU APPROVE'),
          ...pending.memories.map(pendingRow),
          el('div', { class: 'row-gap' }, all),
        );
      } else review.replaceChildren();
      if (!memories.length) {
        list.replaceChildren(el('div', { class: 'empty' }, el('div', { class: 'empty-h' }, 'NOTHING REMEMBERED YET'), el('div', { class: 'empty-b' }, 'Say “remember that …” in Chat, or add a fact above. Lasting facts from your chats are also saved after you stop talking for a few minutes.')));
        return;
      }
      const groups = new Map();
      for (const m of memories) groups.set(m.kind || 'fact', [...(groups.get(m.kind || 'fact') || []), m]);
      list.replaceChildren(...[...groups].flatMap(([kind, items]) => [el('div', { class: 'mem-group' }, kind.toUpperCase()), ...items.map(row)]));
    } catch (e) {
      fail(e);
    }
  }

  async function addOne() {
    const text = add.value.trim();
    if (!text) return;
    try {
      await api.addMemory(text);
      add.value = '';
      load();
    } catch (e) {
      fail(e);
    }
  }

  addBtn.addEventListener('click', addOne);
  add.addEventListener('keydown', (e) => { if (e.key === 'Enter') addOne(); });
  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  wipeBtn.addEventListener('click', () => {
    if (!confirm('Delete everything the assistant remembers? This cannot be undone.')) return;
    api.wipeMemory().then(load, fail);
  });
  exportBtn.addEventListener('click', async () => {
    try {
      const data = await api.exportMemory();
      const a = el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })), download: 'memories.json' });
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      fail(e);
    }
  });

  load();
}
