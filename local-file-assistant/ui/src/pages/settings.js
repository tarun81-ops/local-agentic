import { api } from '../api.js';
import { el, notice } from '../components/common.js';

function kv(label, ...value) {
  return el('div', { class: 'kv' }, el('div', { class: 'kv-k' }, label), el('div', { class: 'kv-v' }, ...value));
}

function sectionHead(n, title) {
  return el('div', { class: 'section-h' }, el('h2', {}, el('span', { class: 'n' }, `${n} —`), ` ${title}`));
}

export function render(container, { onStatusChanged }) {
  const model = el('div', { class: 'box box-pad' }, el('div', { class: 'quiet' }, 'Loading…'));
  const flash = el('div');
  const toggle = el('input', { type: 'checkbox', id: 'launch-at-startup', class: 'toggle', role: 'switch' });
  const shortcut = el('span', { class: 'keys' });

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('h1', { class: 'page-title' }, 'SETTINGS')),
    el(
      'div',
      { class: 'page-body sections' },
      flash,
      el('section', {}, sectionHead('01', 'MODEL & BACKEND'), model),
      el(
        'div',
        { class: 'two-col' },
        el(
          'section',
          {},
          sectionHead('02', 'GLOBAL SHORTCUT'),
          el(
            'div',
            { class: 'box box-pad stack' },
            el('div', { class: 'kv kv-plain' }, el('div', {}, 'SUMMON OVERLAY'), shortcut),
            el('div', { class: 'kv kv-plain' }, el('label', { for: 'launch-at-startup' }, 'LAUNCH AT STARTUP'), toggle),
          ),
        ),
        el(
          'section',
          {},
          sectionHead('03', 'PRIVACY'),
          el(
            'div',
            { class: 'privacy' },
            el('div', { class: 'privacy-h' }, 'PRIVATE BY DESIGN'),
            el('div', {}, 'Nothing you index or ask ever leaves this device. No cloud calls, no telemetry.'),
          ),
        ),
      ),
    ),
  );

  window.lfa.shortcut().then((keys) =>
    shortcut.replaceChildren(...keys.split('+').flatMap((k, i) => [i ? el('span', { class: 'plus' }, '+') : '', el('kbd', {}, k.toUpperCase())])),
  );
  window.lfa.getLaunchAtStartup().then(({ enabled, supported }) => {
    toggle.checked = enabled;
    toggle.disabled = !supported;
    if (!supported) toggle.title = 'Available in the installed app';
  });
  toggle.addEventListener('change', () => window.lfa.setLaunchAtStartup(toggle.checked));

  async function load() {
    try {
      const s = await api.settings();
      const picker = el('select', { class: 'field field-mono', id: 'model-select', 'aria-label': 'Model' });
      const models = s.ollama.models.includes(s.model) ? s.ollama.models : [s.model, ...s.ollama.models];
      picker.append(...models.map((m) => el('option', { value: m, selected: m === s.model }, m)));
      const save = el('button', { type: 'button', class: 'link-btn', disabled: true }, 'CHANGE');
      picker.addEventListener('change', () => (save.disabled = picker.value === s.model));
      save.addEventListener('click', async () => {
        try {
          await api.setModel(picker.value);
          flash.replaceChildren(notice(`Now answering with ${picker.value}.`));
          onStatusChanged?.();
          load();
        } catch (e) {
          flash.replaceChildren(notice(e.message, 'error'));
        }
      });
      model.replaceChildren(
        kv('BASE URL', el('span', { class: 'mono' }, s.base_url)),
        kv('MODEL', picker, save),
        kv('EMBEDDING MODEL', el('span', { class: 'mono' }, s.embedding_model)),
        kv(
          'CONNECTION',
          s.ollama.connected
            ? el('span', { class: 'conn' }, el('span', { class: 'status-dot' }), 'CONNECTED')
            : el('span', { class: 'conn conn-off' }, 'NOT RUNNING — start Ollama, then reopen this page'),
        ),
        kv('FILE TYPES', el('span', { class: 'mono' }, s.file_types.join('  '))),
        kv('DATA FOLDER', el('span', { class: 'mono' }, s.data_dir)),
        s.memory?.free_mb != null
          ? kv(
              'FREE MEMORY',
              el(
                'span',
                { class: `mono ${s.memory.low ? 'conn-off' : ''}` },
                `${(s.memory.free_mb / 1024).toFixed(1)} GB of ${(s.memory.total_mb / 1024).toFixed(1)} GB`,
                s.memory.low ? ' — LOW: indexing skips embeddings until more is free' : '',
              ),
            )
          : '',
      );
    } catch (e) {
      model.replaceChildren(notice(e.message, 'error'));
    }
  }
  load();
}
