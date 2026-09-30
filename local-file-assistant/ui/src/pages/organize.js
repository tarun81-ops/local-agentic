import { api } from '../api.js';
import { el, fileName, notice, relPath, svg, ICONS } from '../components/common.js';

const SHOWN_PER_GROUP = 5;

function checkbox(checked, label, onchange) {
  const box = el('input', { type: 'checkbox', class: 'check', 'aria-label': label, onchange });
  box.checked = checked;
  return box;
}

export function render(container, { navigate }) {
  const folder = el('select', { id: 'organize-root', class: 'field', 'aria-label': 'Folder to organize' });
  const propose = el('button', { type: 'button', class: 'btn btn-ink' }, 'PROPOSE PLAN');
  const reject = el('button', { type: 'button', class: 'btn btn-outline', disabled: true }, 'REJECT');
  const approve = el('button', { type: 'button', class: 'btn btn-primary', disabled: true }, 'APPROVE & APPLY');
  const body = el('div', { class: 'page-body plan' });
  let plan = null;
  let selected = new Set();

  container.replaceChildren(
    el(
      'header',
      { class: 'page-head' },
      el('div', {}, el('h1', { class: 'page-title' }, 'ORGANIZE'), el('div', { class: 'page-sub' }, 'AI-PROPOSED PLAN — NOTHING CHANGES UNTIL YOU APPROVE')),
      el('div', { class: 'head-actions' }, reject, approve),
    ),
    el('div', { class: 'toolbar' }, el('label', { class: 'toolbar-label', for: 'organize-root' }, 'FOLDER'), folder, propose),
    body,
    el(
      'div',
      { class: 'banner-blue' },
      el('span', { class: 'badge badge-blue' }, 'REVERSIBLE'),
      el('span', {}, 'Moves can be undone anytime from Index → History. Deleted files go to the Recycle Bin.'),
    ),
  );

  function setButtons() {
    approve.disabled = !plan || selected.size === 0;
    reject.disabled = !plan;
    approve.textContent = plan ? `APPROVE & APPLY (${selected.size})` : 'APPROVE & APPLY';
  }

  function idle(message) {
    plan = null;
    selected = new Set();
    setButtons();
    body.replaceChildren(message);
  }

  function groupView(name, actions, root) {
    let expanded = actions.length <= SHOWN_PER_GROUP;
    const list = el('div', { class: 'plan-rows' });
    const all = checkbox(actions.every((a) => selected.has(a)), `Include all in ${name}`, () => {
      actions.forEach((a) => (all.checked ? selected.add(a) : selected.delete(a)));
      draw();
      setButtons();
    });
    function draw() {
      const shown = expanded ? actions : actions.slice(0, SHOWN_PER_GROUP);
      list.replaceChildren(
        ...shown.map((a) =>
          el(
            'label',
            { class: 'plan-row' },
            checkbox(selected.has(a), `Include ${fileName(a.path)}`, (e) => {
              if (e.target.checked) selected.add(a);
              else selected.delete(a);
              all.checked = actions.every((x) => selected.has(x));
              setButtons();
            }),
            el('span', { class: 'plan-from' }, relPath(a.path, root)),
            svg(ICONS.arrow, 13),
            a.op === 'move'
              ? el('span', { class: 'plan-to' }, relPath(a.to, root))
              : el('span', { class: 'plan-to plan-trash' }, `RECYCLE BIN — ${a.reason || ''}`),
          ),
        ),
        expanded
          ? ''
          : el('button', { type: 'button', class: 'more', onclick: () => { expanded = true; draw(); } }, `+ ${actions.length - SHOWN_PER_GROUP} MORE FILES`),
      );
    }
    draw();
    return el(
      'section',
      { class: 'plan-group' },
      el(
        'div',
        { class: 'plan-head' },
        el('h2', { class: 'plan-name' }, name.toUpperCase().replaceAll('/', ' / ')),
        el('span', { class: 'count' }, `${actions.length} FILE${actions.length > 1 ? 'S' : ''}`),
        el('label', { class: 'include-all' }, all, 'INCLUDE ALL'),
      ),
      list,
    );
  }

  function showPlan(p) {
    plan = p;
    selected = new Set(p.actions);
    setButtons();
    if (!p.actions.length) {
      body.replaceChildren(notice(p.summary || 'No changes needed. This folder already looks organized.'));
      return;
    }
    const groups = new Map();
    for (const a of p.actions) {
      const key = a.group || 'Other';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(a);
    }
    body.replaceChildren(
      el('div', { class: 'plan-summary' }, p.summary, p.dropped_invalid ? ` (${p.dropped_invalid} unusable suggestion${p.dropped_invalid > 1 ? 's' : ''} skipped)` : ''),
      ...[...groups].map(([name, actions]) => groupView(name, actions, p.root)),
    );
  }

  propose.addEventListener('click', async () => {
    if (!folder.value) return;
    propose.disabled = true;
    idle(notice('Reading the folder and asking the local model for a plan. This can take a minute on CPU…'));
    try {
      showPlan(await api.plan(folder.value));
    } catch (e) {
      idle(notice(e.message, 'error'));
    } finally {
      propose.disabled = false;
    }
  });

  reject.addEventListener('click', () => idle(notice('Plan discarded. Nothing was changed.')));

  approve.addEventListener('click', async () => {
    const actions = plan.actions.filter((a) => selected.has(a));
    approve.disabled = reject.disabled = true;
    try {
      const res = await api.apply(actions);
      const failed = res.results.filter((r) => !r.ok);
      const moved = res.results.filter((r) => r.ok && r.op === 'move').length;
      const binned = res.results.filter((r) => r.ok && r.op === 'delete').length;
      const undo = el('button', { type: 'button', class: 'btn btn-outline' }, 'UNDO THESE MOVES');
      undo.addEventListener('click', async () => {
        undo.disabled = true;
        try {
          const u = await api.undo(res.batch);
          const back = u.results.filter((r) => r.ok).length;
          undo.replaceWith(notice(`Moved ${back} file${back === 1 ? '' : 's'} back.`));
        } catch (e) {
          undo.replaceWith(notice(e.message, 'error'));
        }
      });
      idle(
        el(
          'div',
          { class: 'applied' },
          notice(`Done: ${moved} moved, ${binned} sent to the Recycle Bin.${failed.length ? ` ${failed.length} couldn't be changed.` : ''}`, failed.length ? 'error' : 'neutral'),
          ...failed.map((f) => el('div', { class: 'fail-row' }, `${fileName(f.path)}: ${f.error}`)),
          moved ? undo : '',
        ),
      );
    } catch (e) {
      const problems = e.detail?.problems || [];
      body.prepend(notice(`${e.message}${problems.length ? ' ' + problems.join('; ') : ''}`, 'error'));
      setButtons();
    }
  });

  api.roots().then(({ roots }) => {
    if (!roots.length) {
      folder.disabled = propose.disabled = true;
      idle(
        el(
          'div',
          { class: 'empty' },
          el('div', { class: 'empty-h' }, 'NO FOLDERS INDEXED YET'),
          el('div', { class: 'empty-b' }, 'Add a folder on the Index page first; the plan is built from what’s indexed.'),
          el('button', { type: 'button', class: 'btn btn-primary', onclick: () => navigate('index') }, 'GO TO INDEX'),
        ),
      );
      return;
    }
    folder.replaceChildren(...roots.map((r) => el('option', { value: r.path }, r.path)));
    idle(notice('Choose a folder and press Propose plan. You’ll review every move before anything happens.'));
  }).catch((e) => idle(notice(e.message, 'error')));
}
