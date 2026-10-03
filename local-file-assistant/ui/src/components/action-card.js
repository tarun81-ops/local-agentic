// A confirm card: shows exactly what will happen and runs it only when CONFIRM is clicked.
// Used for reminders/events proposed in chat (and, later, for PC actions).
import { el } from './common.js';

const pad = (n) => String(n).padStart(2, '0');

/** Epoch seconds -> the "YYYY-MM-DDTHH:mm" a datetime-local input wants, in local time. */
export function localInput(sec) {
  if (sec == null) return '';
  const d = new Date(sec * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Epoch seconds -> "Sat 4 Oct, 5:00 PM" (or without the time for all-day). */
export function whenText(sec, allDay = false) {
  if (sec == null) return 'no time';
  const d = new Date(sec * 1000);
  const day = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  return allDay ? day : `${day}, ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
}

/**
 * @param {{title:string, lines?:Array<string|Node>, fields?:Node[], confirmLabel?:string,
 *          onConfirm:()=>Promise<string|void>, onCancel?:()=>Promise<void>, onUndo?:()=>Promise<string|void>}} opts
 *          onConfirm returns the success message; throwing shows the error and lets the user try again.
 *          onUndo (optional) adds an UNDO button once the action has run.
 */
export function actionCard({ title, lines = [], fields = [], confirmLabel = 'CONFIRM', onConfirm, onCancel, onUndo }) {
  const status = el('div', { class: 'action-card-status', role: 'status' });
  const confirm = el('button', { type: 'button', class: 'btn btn-primary btn-sm' }, confirmLabel);
  const cancel = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'CANCEL');
  const card = el(
    'div',
    { class: 'action-card' },
    el('div', { class: 'action-card-h' }, title),
    ...lines.map((l) => el('div', { class: 'action-card-line' }, l)),
    ...fields,
    el('div', { class: 'action-card-btns' }, confirm, cancel),
    status,
  );
  const settle = (text) => {
    confirm.disabled = cancel.disabled = true;
    card.classList.add('settled');
    status.textContent = text;
  };
  confirm.addEventListener('click', async () => {
    confirm.disabled = cancel.disabled = true;
    try {
      settle((await onConfirm()) || 'DONE');
      if (onUndo) {
        const undo = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'UNDO');
        undo.addEventListener('click', async () => {
          undo.disabled = true;
          try {
            status.textContent = (await onUndo()) || 'UNDONE';
            undo.remove();
          } catch (e) {
            status.textContent = e.message;
            undo.disabled = false;
          }
        });
        card.querySelector('.action-card-btns').append(undo);
      }
    } catch (e) {
      status.textContent = e.message;
      confirm.disabled = cancel.disabled = false;
    }
  });
  cancel.addEventListener('click', async () => {
    cancel.disabled = confirm.disabled = true;
    await onCancel?.().catch(() => {});
    settle('CANCELLED');
  });
  return card;
}
