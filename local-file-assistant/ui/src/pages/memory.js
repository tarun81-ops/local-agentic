import { api } from '../api.js';
import { el, notice } from '../components/common.js';

/** What the assistant remembers about you. Facts come only from you: "remember that …" in Chat, or
 *  the box below. Nothing is guessed from your chats. Each fact can be edited or deleted. */
export function render(container) {
  const add = el('input', { type: 'text', class: 'field', placeholder: 'Add something to remember…', 'aria-label': 'New memory' });
  const addBtn = el('button', { type: 'button', class: 'btn btn-ink' }, 'Add');
  const wipeBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'Forget everything');
  const flash = el('div');
  const list = el('div');

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'Memory'), el('div', { class: 'page-sub' }, 'What I remember about you — only what you tell me, stored on this device')), el('div', { class: 'head-actions' }, wipeBtn)),
    el('div', { class: 'toolbar' }, add, addBtn),
    el('div', { class: 'page-body' }, flash, list),
  );

  const fail = (e) => flash.replaceChildren(notice(e.message, 'error'));

  function row(m) {
    const text = el('div', { class: 'mem-text' }, m.text);
    const edit = el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Edit: ${m.text}` }, 'Edit');
    const del = el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Delete: ${m.text}`, onclick: () => api.deleteMemory(m.id).then(load, fail) }, 'DELETE');
    edit.addEventListener('click', () => {
      const input = el('input', { type: 'text', class: 'field', value: m.text, 'aria-label': 'Edit memory' });
      const save = () => api.updateMemory(m.id, { text: input.value }).then(load, fail);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') load(); });
      text.replaceChildren(input);
      input.focus();
    });
    return el('div', { class: 'mem-row' }, text, el('div', { class: 'mem-actions' }, edit, del));
  }

  async function load() {
    flash.replaceChildren();
    try {
      const { memories } = await api.memories('');
      wipeBtn.disabled = !memories.length;
      list.replaceChildren(
        ...(memories.length
          ? memories.map(row)
          : [el('div', { class: 'empty' }, el('div', { class: 'empty-h' }, 'Nothing remembered yet'), el('div', { class: 'empty-b' }, 'Say “remember that …” in Chat, or add a fact above. Nothing is saved from your chats unless you ask.'))]),
      );
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
  wipeBtn.addEventListener('click', () => {
    if (!confirm('Forget everything I remember about you? This cannot be undone.')) return;
    api.wipeMemory().then(load, fail);
  });

  load();
}
