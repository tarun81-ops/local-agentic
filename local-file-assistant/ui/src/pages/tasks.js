import { api } from '../api.js';
import { whenText } from '../components/action-card.js';
import { el, notice } from '../components/common.js';

const DAY = 86400;

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.getTime() / 1000;
}

/** Tasks and reminders, plus a 7-day strip of events and due dates. */
export function render(container) {
  const quick = el('input', { type: 'text', class: 'field', placeholder: 'Remind me to call mom tomorrow at 5pm…', 'aria-label': 'Add a task or reminder' });
  const addBtn = el('button', { type: 'button', class: 'btn btn-ink' }, 'ADD');
  const exportBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'EXPORT .ICS');
  const importBtn = el('button', { type: 'button', class: 'btn btn-outline' }, 'IMPORT .ICS');
  const file = el('input', { type: 'file', accept: '.ics,text/calendar', hidden: true });
  const flash = el('div');
  const strip = el('div', { class: 'cal-strip' });
  const lists = el('div', { class: 'page-body' });

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('div', {}, el('h1', { class: 'page-title' }, 'TASKS'), el('div', { class: 'page-sub' }, 'REMINDERS FIRE AS DESKTOP NOTIFICATIONS WHILE THE APP RUNS IN THE TRAY')), el('div', { class: 'head-actions' }, exportBtn, importBtn, file)),
    el('div', { class: 'toolbar' }, quick, addBtn),
    flash,
    strip,
    lists,
  );

  const fail = (e) => flash.replaceChildren(notice(e.message, 'error'));

  function row(t) {
    const done = t.status === 'done';
    const check = el('input', { type: 'checkbox', class: 'check', 'aria-label': `Mark done: ${t.title}`, onchange: () => api.completeTask(t.id).then(load, fail) });
    check.checked = done;
    const when = t.due_at ?? t.remind_at;
    return el(
      'div',
      { class: `task-row ${done ? 'task-done' : ''}` },
      check,
      el('div', { class: 'task-main' }, el('div', { class: 'task-title' }, t.title), el('div', { class: 'task-when' }, [when != null ? whenText(when) : 'NO DATE', t.repeat ? `REPEATS ${t.repeat.toUpperCase()}` : ''].filter(Boolean).join(' · '))),
      done
        ? ''
        : el('button', { type: 'button', class: 'btn btn-outline btn-sm', onclick: () => api.snoozeTask(t.id, 10).then(load, fail) }, 'SNOOZE 10 MIN'),
      el('button', { type: 'button', class: 'btn btn-outline btn-sm', 'aria-label': `Delete: ${t.title}`, onclick: () => api.deleteTask(t.id).then(load, fail) }, 'DELETE'),
    );
  }

  function section(title, items) {
    return items.length ? [el('div', { class: 'mem-group' }, title), ...items.map(row)] : [];
  }

  function drawStrip(open, events, today) {
    strip.replaceChildren(
      ...Array.from({ length: 7 }, (_, i) => {
        const from = today + i * DAY;
        const to = from + DAY;
        const items = [
          ...events.filter((e) => e.start_at < to && e.end_at > from).map((e) => `${e.all_day ? '' : whenText(e.start_at).split(', ')[1] + ' '}${e.title}`),
          ...open.filter((t) => (t.due_at ?? t.remind_at) >= from && (t.due_at ?? t.remind_at) < to).map((t) => `☐ ${t.title}`),
        ];
        const label = new Date(from * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }).toUpperCase();
        return el('div', { class: `cal-day ${i === 0 ? 'cal-today' : ''}` }, el('div', { class: 'cal-h' }, label), ...items.slice(0, 4).map((x) => el('div', { class: 'cal-item' }, x)), items.length > 4 ? el('div', { class: 'cal-item' }, `+${items.length - 4} more`) : '');
      }),
    );
  }

  async function load() {
    flash.replaceChildren();
    try {
      const today = startOfToday();
      const [{ tasks }, { events }] = await Promise.all([api.tasks(), api.events(today, today + 7 * DAY)]);
      const open = tasks.filter((t) => t.status === 'open');
      const dated = (t) => t.due_at ?? t.remind_at;
      const overdue = open.filter((t) => dated(t) != null && dated(t) < today);
      const todayList = open.filter((t) => dated(t) != null && dated(t) >= today && dated(t) < today + DAY);
      const upcoming = open.filter((t) => dated(t) != null && dated(t) >= today + DAY);
      const undated = open.filter((t) => dated(t) == null);
      const done = tasks.filter((t) => t.status === 'done').slice(-10).reverse();
      drawStrip(open, events, today);
      const all = [...section('OVERDUE', overdue), ...section('TODAY', todayList), ...section('UPCOMING', upcoming), ...section('NO DATE', undated), ...section('DONE', done)];
      lists.replaceChildren(...(all.length ? all : [el('div', { class: 'empty' }, el('div', { class: 'empty-h' }, 'NOTHING TO DO'), el('div', { class: 'empty-b' }, 'Add a task above, or tell Chat “remind me to … tomorrow at 5pm”.'))]));
    } catch (e) {
      fail(e);
    }
  }

  async function add() {
    const text = quick.value.trim();
    if (!text) return;
    try {
      const p = await api.parseTask(text);
      await api.createTask({ title: p.title, due_at: p.when, remind_at: p.when, repeat: p.repeat });
      quick.value = '';
      load();
      if (p.when != null && p.confidence === 'date_only') flash.replaceChildren(notice(`Added for ${whenText(p.when)}. No time was given, so 9:00 was assumed.`));
    } catch (e) {
      fail(e);
    }
  }

  addBtn.addEventListener('click', add);
  quick.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
  exportBtn.addEventListener('click', async () => {
    try {
      const a = el('a', { href: URL.createObjectURL(new Blob([await api.exportIcs()], { type: 'text/calendar' })), download: 'tasks.ics' });
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      fail(e);
    }
  });
  importBtn.addEventListener('click', () => file.click());
  file.addEventListener('change', async () => {
    try {
      const r = await api.importIcs(await file.files[0].text());
      flash.replaceChildren(notice(`Imported ${r.tasks} task(s) and ${r.events} event(s).`));
      load();
    } catch (e) {
      fail(e);
    }
    file.value = '';
  });

  load();
}
