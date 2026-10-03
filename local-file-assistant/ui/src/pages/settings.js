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
  const shortcutInput = el('input', { type: 'text', class: 'shortcut-input', placeholder: 'e.g. Ctrl+Alt+Space', 'aria-label': 'New shortcut' });
  const shortcutSet = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SET');
  const meBox = el('div', { class: 'box box-pad stack' }, el('div', { class: 'quiet' }, 'Loading…'));
  const proBox = el('div', { class: 'box box-pad stack' }, el('div', { class: 'quiet' }, 'Loading…'));
  const voiceBox = el('div', { class: 'box box-pad stack' }, el('div', { class: 'quiet' }, 'Loading…'));
  const learnBox = el('div', { class: 'box box-pad stack' }, el('div', { class: 'quiet' }, 'Loading…'));

  container.replaceChildren(
    el('header', { class: 'page-head' }, el('h1', { class: 'page-title' }, 'SETTINGS')),
    el(
      'div',
      { class: 'page-body sections' },
      flash,
      el('section', {}, sectionHead('00', 'ABOUT ME & ANSWERS'), meBox),
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
            el('div', { class: 'kv kv-plain' }, el('div', {}, 'SUMMON OVERLAY'), el('div', { class: 'row-gap' }, shortcut, shortcutInput, shortcutSet)),
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
      el('section', {}, sectionHead('04', 'PROACTIVE SUGGESTIONS'), proBox),
      el('section', {}, sectionHead('05', 'VOICE'), voiceBox),
      el('section', {}, sectionHead('06', 'LEARNING FROM USE'), learnBox),
    ),
  );

  const showKeys = (keys) => shortcut.replaceChildren(...keys.split('+').flatMap((k, i) => [i ? el('span', { class: 'plus' }, '+') : '', el('kbd', {}, k.toUpperCase())]));
  window.lfa.shortcut().then(showKeys);
  shortcutSet.addEventListener('click', async () => {
    const r = await window.lfa.setShortcut(shortcutInput.value);
    if (r.ok) {
      showKeys(r.shortcut);
      shortcutInput.value = '';
      flash.replaceChildren();
    } else flash.replaceChildren(notice(r.error, 'error'));
  });
  window.lfa.getLaunchAtStartup().then(({ enabled, supported }) => {
    toggle.checked = enabled;
    toggle.disabled = !supported;
    if (!supported) toggle.title = 'Available in the installed app';
  });
  toggle.addEventListener('change', () => window.lfa.setLaunchAtStartup(toggle.checked));

  // Morning briefing and nudges: each can be switched off, with quiet hours and a daily limit.
  async function loadProactive() {
    try {
      const c = await api.proactiveSettings();
      const field = (id, label, key, attrs, value) => {
        const input = el('input', { id, ...attrs });
        if (attrs.type === 'checkbox') input.checked = value;
        else input.value = value;
        input.dataset.key = key;
        return el('div', { class: 'kv kv-plain' }, el('label', { for: id }, label), input);
      };
      const sw = { type: 'checkbox', class: 'toggle', role: 'switch' };
      const time = { type: 'time', class: 'shortcut-input' };
      const save = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SAVE');
      const preview = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'PREVIEW TODAY’S BRIEFING');
      const out = el('div');
      const rows = [
        field('p-briefing', 'MORNING BRIEFING', 'briefing', sw, c.briefing),
        field('p-btime', 'BRIEFING TIME', 'briefing_time', time, c.briefing_time),
        field('p-overdue', 'EVENING NUDGE FOR OVERDUE TASKS', 'overdue', sw, c.overdue),
        field('p-qs', 'QUIET HOURS FROM', 'quiet_start', time, c.quiet_start),
        field('p-qe', 'QUIET HOURS UNTIL', 'quiet_end', time, c.quiet_end),
        field('p-cap', 'MOST SUGGESTIONS PER DAY', 'daily_cap', { type: 'number', min: 0, max: 10, class: 'shortcut-input' }, c.daily_cap),
        field('p-model', 'LET THE LOCAL MODEL REWORD THE BRIEFING (USES MORE RAM)', 'use_model', sw, c.use_model),
      ];
      proBox.replaceChildren(...rows, el('div', { class: 'row-gap' }, save, preview), out);
      save.addEventListener('click', async () => {
        const body = {};
        for (const input of proBox.querySelectorAll('input[data-key]')) {
          body[input.dataset.key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
        }
        try {
          await api.saveProactive(body);
          out.replaceChildren(notice('Saved.'));
        } catch (e) {
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
      preview.addEventListener('click', async () => {
        try {
          const p = await api.proactivePreview();
          out.replaceChildren(notice(p.empty ? 'Nothing to report today: no tasks, events or changed files.' : p.text));
        } catch (e) {
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
    } catch (e) {
      proBox.replaceChildren(notice(e.message, 'error'));
    }
  }
  // Voice: the speech models are downloaded once (a few hundred MB); everything then runs on this PC.
  async function loadVoice(errors = {}) {
    try {
      const [s, keys] = await Promise.all([api.voiceStatus(), window.lfa.shortcuts()]);
      const ready = (ok) => (ok ? 'READY' : 'NOT DOWNLOADED');
      const input = el('input', { type: 'text', class: 'shortcut-input', placeholder: 'e.g. Ctrl+Shift+Alt+V', 'aria-label': 'New voice shortcut' });
      const set = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SET');
      const keyCaps = (text) => el('span', { class: 'keys' }, ...text.split('+').flatMap((k, i) => [i ? el('span', { class: 'plus' }, '+') : '', el('kbd', {}, k.toUpperCase())]));
      const download = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'DOWNLOAD VOICE MODELS');
      download.disabled = s.stt.available && s.tts.available;
      const out = el('div', {}, ...Object.entries(errors).map(([k, v]) => notice(`${k === 'stt' ? 'Speech recognition' : 'Voice'}: ${v}`, 'error')));
      voiceBox.replaceChildren(
        el('div', { class: 'kv kv-plain' }, el('div', {}, `SPEECH RECOGNITION (${s.stt.model})`), el('div', {}, ready(s.stt.available))),
        el('div', { class: 'kv kv-plain' }, el('div', {}, `SPOKEN ANSWERS (${s.tts.voice})`), el('div', {}, ready(s.tts.available))),
        el('div', { class: 'kv kv-plain' }, el('div', {}, 'TALK TO THE ASSISTANT'), el('div', { class: 'row-gap' }, keyCaps(keys.voice), input, set)),
        el('div', { class: 'row-gap' }, download),
        out,
      );
      set.addEventListener('click', async () => {
        const r = await window.lfa.setShortcut(input.value, 'voice');
        if (r.ok) loadVoice();
        else out.replaceChildren(notice(r.error, 'error'));
      });
      download.addEventListener('click', async () => {
        download.disabled = true;
        out.replaceChildren(notice('Downloading… this can take a few minutes. Keep the app open.'));
        try {
          loadVoice((await api.downloadVoice()).errors);
        } catch (e) {
          download.disabled = false;
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
    } catch (e) {
      voiceBox.replaceChildren(notice(e.message, 'error'));
    }
  }
  // Learning from use: files you open for a search rise slightly in similar searches, and answers you
  // mark not helpful can be exported as test questions. Everything stays on this PC and can be wiped.
  async function loadLearning() {
    try {
      const s = await api.learningStats();
      const exportBtn = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'EXPORT NOT-HELPFUL QUESTIONS');
      const wipeBtn = el('button', { type: 'button', class: 'btn btn-outline btn-sm' }, 'FORGET WHAT I OPEN AND RATE');
      exportBtn.disabled = s.not_helpful === 0;
      const out = el('div');
      learnBox.replaceChildren(
        el('div', { class: 'kv kv-plain' }, el('div', {}, 'FILE OPENS REMEMBERED'), el('div', {}, String(s.opens))),
        el('div', { class: 'kv kv-plain' }, el('div', {}, 'ANSWERS MARKED HELPFUL / NOT HELPFUL'), el('div', {}, `${s.helpful} / ${s.not_helpful}`)),
        el('div', { class: 'row-gap' }, exportBtn, wipeBtn),
        out,
      );
      exportBtn.addEventListener('click', async () => {
        try {
          const a = el('a', { href: URL.createObjectURL(new Blob([await api.exportLearning()], { type: 'application/x-ndjson' })), download: 'not-helpful-questions.jsonl' });
          a.click();
          URL.revokeObjectURL(a.href);
          out.replaceChildren(notice('Saved. Append the lines to backend/eval/questions.jsonl and fill in expect and file for the ones you want measured.'));
        } catch (e) {
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
      wipeBtn.addEventListener('click', async () => {
        if (!confirm('Forget which files you opened and which answers you rated? Search ranking returns to its defaults.')) return;
        try {
          await api.wipeLearning();
          loadLearning();
        } catch (e) {
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
    } catch (e) {
      learnBox.replaceChildren(notice(e.message, 'error'));
    }
  }
  // Personalization: what the assistant knows about you and how it answers. Everything is optional and stays on this PC.
  async function loadMe() {
    try {
      const c = await api.personalize();
      const profile = el('textarea', { id: 'pz-profile', class: 'field', rows: 4, maxlength: 800, placeholder: 'e.g. First-year ETE student in Raipur. Working on a robotics project. I like short answers with examples.' });
      profile.value = c.profile;
      const count = el('div', { class: 'quiet' }, `${c.profile.length} / 800`);
      profile.addEventListener('input', () => (count.textContent = `${profile.value.length} / 800`));
      const select = (id, label, key, options, value) => {
        const input = el('select', { id, class: 'field field-mono' }, ...options.map(([v, t]) => el('option', { value: v, selected: v === value }, t)));
        input.dataset.key = key;
        return el('div', { class: 'kv kv-plain' }, el('label', { for: id }, label), input);
      };
      const sw = (id, label, key, value) => {
        const input = el('input', { id, type: 'checkbox', class: 'toggle', role: 'switch' });
        input.checked = value;
        input.dataset.key = key;
        return el('div', { class: 'kv kv-plain' }, el('label', { for: id }, label), input);
      };
      const save = el('button', { type: 'button', class: 'btn btn-ink btn-sm' }, 'SAVE');
      const out = el('div');
      meBox.replaceChildren(
        el('label', { for: 'pz-profile' }, 'ABOUT ME (SHARED WITH THE LOCAL MODEL ON EVERY QUESTION; NEVER LEAVES THIS PC)'),
        profile,
        count,
        select('pz-style', 'ANSWER STYLE', 'answer_style', [['concise', 'CONCISE'], ['detailed', 'DETAILED'], ['study', 'STUDY'], ['simple', 'SIMPLE']], c.answer_style),
        select('pz-lang', 'LANGUAGE', 'language', [['auto', 'MATCH WHAT I WRITE'], ['english', 'ENGLISH'], ['hinglish', 'HINGLISH']], c.language),
        sw('pz-cite', 'ALWAYS CITE PAGE NUMBERS', 'cite_pages', c.cite_pages),
        select('pz-strict', 'CITATION CHECKING', 'verifier_strict', [['normal', 'NORMAL'], ['strict', 'STRICT (FLAGS MORE CLAIMS)']], c.verifier_strict),
        sw('pz-review', 'ASK ME BEFORE SAVING WHAT I LEARN FROM CHATS', 'memory_review', c.memory_review),
        el('div', { class: 'row-gap' }, save),
        out,
      );
      save.addEventListener('click', async () => {
        const body = { profile: profile.value };
        for (const input of meBox.querySelectorAll('[data-key]')) body[input.dataset.key] = input.type === 'checkbox' ? input.checked : input.value;
        try {
          await api.savePersonalize(body);
          out.replaceChildren(notice('Saved.'));
        } catch (e) {
          out.replaceChildren(notice(e.message, 'error'));
        }
      });
    } catch (e) {
      meBox.replaceChildren(notice(e.message, 'error'));
    }
  }
  loadMe();
  loadLearning();
  loadVoice();
  loadProactive();

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
