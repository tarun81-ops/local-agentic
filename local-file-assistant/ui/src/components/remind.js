import { api } from '../api.js';
import { actionCard, localInput, whenText } from './action-card.js';
import { el, fileName, notice } from './common.js';

/**
 * REMIND ME about a file: type when and what ("review tomorrow 5pm"), check the parsed proposal,
 * and confirm. The reminder keeps the file, so its notification and the Tasks page can open it.
 */
export function openRemind(path, name = fileName(path)) {
  const dialog = el('dialog', { class: 'study-dialog', 'aria-label': `Remind me about ${name}` });
  const text = el('input', { type: 'text', class: 'field', value: `Review ${name} tomorrow 9am`, 'aria-label': 'What and when' });
  const go = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'NEXT');
  const close = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'CLOSE');
  const stage = el('div', { class: 'study-stage', 'aria-live': 'polite' });
  dialog.append(
    el('div', { class: 'study-head' }, el('div', { class: 'section-h' }, el('h2', {}, `REMIND ME — ${name.toUpperCase()}`)), close),
    el('div', { class: 'row-gap' }, text, go),
    stage,
  );

  async function propose() {
    stage.replaceChildren();
    try {
      const p = await api.parseTask(text.value.trim() || `Review ${name}`);
      const title = el('input', { type: 'text', class: 'field', value: p.title, 'aria-label': 'Title' });
      const when = el('input', { type: 'datetime-local', class: 'field', value: localInput(p.when), 'aria-label': 'Remind at' });
      const repeat = el('select', { class: 'field', 'aria-label': 'Repeat' }, ['', 'daily', 'weekly', 'monthly'].map((v) => el('option', { value: v }, v ? `REPEAT ${v.toUpperCase()}` : 'NO REPEAT')));
      repeat.value = p.repeat || '';
      stage.append(
        actionCard({
          title: 'SET REMINDER FOR THIS FILE',
          lines: [`File: ${name}`, ...(p.confidence === 'date_only' ? ['No time was given, so 9:00 was assumed. Check it.'] : [])],
          fields: [title, when, repeat],
          onConfirm: async () => {
            const sec = when.value ? new Date(when.value).getTime() / 1000 : null;
            if (!title.value.trim()) throw new Error('Give it a title.');
            await api.createTask({ title: title.value.trim(), due_at: sec, remind_at: sec, repeat: repeat.value || null, file_path: path });
            return sec != null ? `REMINDER SET FOR ${whenText(sec).toUpperCase()}` : 'TASK ADDED';
          },
          onCancel: async () => dialog.close(),
        }),
      );
    } catch (e) {
      stage.replaceChildren(notice(e.message, 'error'));
    }
  }

  go.addEventListener('click', propose);
  text.addEventListener('keydown', (e) => { if (e.key === 'Enter') propose(); });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  text.select();
}
