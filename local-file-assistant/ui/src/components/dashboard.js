// KPI + chart overview for the Index page. Numbers count up with a slight overshoot and
// charts animate in the same way — the house "impact" motion already used for the overlay
// panel (panelIn's cubic-bezier(0.16,1,0.3,1)), just applied to a counter and to Chart.js's
// built-in easeOutBack. Charts render once; later refreshes update them in place with no
// re-animation, so an active scan doesn't replay the impact every second.
import {
  ArcElement, BarController, BarElement, CategoryScale, Chart, DoughnutController, Legend, LinearScale, Tooltip,
} from 'chart.js';
import { el, fileName, fmtDate } from './common.js';

Chart.register(BarController, BarElement, DoughnutController, ArcElement, CategoryScale, LinearScale, Tooltip, Legend);

const INK = '#14120F';
const RED = '#E63312';
const BLUE = '#1D3ED8';
const YELLOW = '#D9A61C';
const INK_FAINT = '#69645A';

// Mirrors common.js's TYPES tones, so a file type is the same color here as its swatch elsewhere.
const TYPE_COLOR = {
  pdf: RED, docx: BLUE, doc: BLUE, xlsx: BLUE, pptx: INK,
  png: YELLOW, jpg: YELLOW, jpeg: YELLOW, webp: YELLOW, bmp: YELLOW,
};

// The global `@media (prefers-reduced-motion: reduce)` rule in style.css only stops CSS
// animation/transition — it can't reach a canvas (Chart.js) or a requestAnimationFrame loop
// (countUp), so those two are gated here instead.
const REDUCE_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const CHART_ANIMATION = REDUCE_MOTION ? false : { duration: 700, easing: 'easeOutBack' };
const FONT = { family: "'Space Mono', monospace", size: 11 };

function splitBytes(n) {
  if (!n) return { value: 0, unit: 'B' };
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return { value: n / 1024 ** i, unit: units[i] };
}

function easeOutBack(t) {
  return 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;
}

function countUp(numEl, to, decimals) {
  if (REDUCE_MOTION) {
    numEl.textContent = to.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return;
  }
  const start = performance.now();
  const duration = 650;
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    const shown = t < 1 ? to * easeOutBack(t) : to;
    numEl.textContent = shown.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function kpiCard(label, value, { decimals = 0, suffix = '', sub = '' } = {}) {
  const num = el('span', {}, '0');
  const card = el(
    'div',
    { class: 'kpi-card' },
    el('div', { class: 'kpi-label' }, label),
    el('div', { class: 'kpi-value' }, num, suffix ? el('span', { class: 'kpi-suffix' }, suffix) : ''),
    sub ? el('div', { class: 'kpi-sub' }, sub) : '',
  );
  requestAnimationFrame(() => countUp(num, value, decimals));
  return card;
}

function chartBox(title, canvas) {
  return el('div', { class: 'chart-box' }, el('h3', {}, title), el('div', { class: 'chart-wrap' }, canvas));
}

function fileTypeCounts(files) {
  const counts = {};
  for (const f of files) {
    const ext = fileName(f.path).split('.').pop().toLowerCase();
    counts[ext] = (counts[ext] || 0) + 1;
  }
  // Top 8 by count, so a library with many odd extensions doesn't crowd the legend.
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
}

/**
 * Builds the overview once and returns update(data) for later lightweight refreshes.
 * data: { roots, batches, files } — files may be omitted on later calls to skip re-fetching
 * (and re-rendering) the file-type chart on every poll during an active scan.
 */
export function mountDashboard(container, { roots, batches, files }) {
  const totalFiles = roots.reduce((n, r) => n + r.files, 0);
  const totalBytes = roots.reduce((n, r) => n + r.bytes, 0);
  const keywordOnly = roots.reduce((n, r) => n + r.keyword_only, 0);
  const storage = splitBytes(totalBytes);

  const kpiFolders = kpiCard('FOLDERS INDEXED', roots.length);
  const kpiFiles = kpiCard('FILES INDEXED', totalFiles);
  const kpiStorage = kpiCard('STORAGE INDEXED', storage.value, { decimals: storage.unit === 'B' ? 0 : 1, suffix: ` ${storage.unit}` });
  const kpiSemantic = kpiCard('SEMANTIC COVERAGE', totalFiles ? Math.round(((totalFiles - keywordOnly) / totalFiles) * 100) : 0, { suffix: '%' });

  const folderCanvas = el('canvas');
  const typeCanvas = el('canvas');
  const organizeCanvas = el('canvas');
  let typeBox = chartBox('FILE TYPES', typeCanvas);
  const organizeBox = el('div');

  container.replaceChildren(
    el('div', { class: 'kpi-row' }, kpiFolders, kpiFiles, kpiStorage, kpiSemantic),
    el(
      'div',
      { class: 'charts-row' },
      chartBox('FILES PER FOLDER', folderCanvas),
      typeBox,
      organizeBox,
    ),
  );

  const folderChart = new Chart(folderCanvas, {
    type: 'bar',
    data: {
      labels: roots.map((r) => fileName(r.path) || r.path),
      datasets: [{ data: roots.map((r) => r.files), backgroundColor: RED, borderRadius: 2 }],
    },
    options: {
      animation: CHART_ANIMATION,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { bodyFont: FONT, titleFont: FONT } },
      scales: {
        x: { ticks: { font: FONT, color: INK_FAINT }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { font: FONT, color: INK_FAINT, precision: 0 }, grid: { color: '#EAE6DC' } },
      },
    },
  });

  let typeChart = null;
  function drawTypes(fileList) {
    const entries = fileTypeCounts(fileList);
    if (!entries.length) {
      const empty = chartBox('FILE TYPES', el('div', { class: 'chart-empty' }, 'Nothing indexed yet.'));
      typeBox.replaceWith(empty);
      typeBox = empty;
      return;
    }
    typeChart = new Chart(typeCanvas, {
      type: 'doughnut',
      data: {
        labels: entries.map(([ext]) => ext.toUpperCase()),
        datasets: [{ data: entries.map(([, n]) => n), backgroundColor: entries.map(([ext]) => TYPE_COLOR[ext] || INK_FAINT), borderColor: '#F5F3EE', borderWidth: 2 }],
      },
      options: {
        animation: CHART_ANIMATION,
        maintainAspectRatio: false,
        cutout: '62%',
        plugins: { legend: { position: 'right', labels: { font: FONT, color: INK, boxWidth: 10, padding: 10 } }, tooltip: { bodyFont: FONT, titleFont: FONT } },
      },
    });
  }

  function drawOrganize(batchList) {
    if (!batchList.length) {
      organizeBox.replaceChildren(el('h3', {}, 'ORGANIZE ACTIVITY'), el('div', { class: 'chart-empty' }, 'No reorganizations yet.'));
      return;
    }
    const chrono = [...batchList].reverse(); // history() is newest-first; charts read left-to-right
    organizeBox.replaceChildren(el('h3', {}, 'ORGANIZE ACTIVITY'), el('div', { class: 'chart-wrap' }, organizeCanvas));
    new Chart(organizeCanvas, {
      type: 'bar',
      data: {
        labels: chrono.map((b) => fmtDate(b.at)),
        datasets: [
          { label: 'MOVED', data: chrono.map((b) => b.moves), backgroundColor: BLUE, stack: 'a' },
          { label: 'RECYCLE BIN', data: chrono.map((b) => b.trashed), backgroundColor: RED, stack: 'a' },
        ],
      },
      options: {
        animation: CHART_ANIMATION,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { font: FONT, color: INK, boxWidth: 10 } }, tooltip: { bodyFont: FONT, titleFont: FONT } },
        scales: {
          x: { stacked: true, ticks: { font: FONT, color: INK_FAINT }, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, ticks: { font: FONT, color: INK_FAINT, precision: 0 }, grid: { color: '#EAE6DC' } },
        },
      },
    });
  }

  drawTypes(files);
  drawOrganize(batches);

  return function update({ roots, batches, files }) {
    const totalFiles = roots.reduce((n, r) => n + r.files, 0);
    const totalBytes = roots.reduce((n, r) => n + r.bytes, 0);
    const keywordOnly = roots.reduce((n, r) => n + r.keyword_only, 0);
    const storage = splitBytes(totalBytes);
    kpiFolders.querySelector('.kpi-value span').textContent = roots.length.toLocaleString();
    kpiFiles.querySelector('.kpi-value span').textContent = totalFiles.toLocaleString();
    kpiStorage.querySelector('.kpi-value span').textContent = storage.value.toLocaleString(undefined, { maximumFractionDigits: storage.unit === 'B' ? 0 : 1 });
    kpiStorage.querySelector('.kpi-suffix').textContent = ` ${storage.unit}`;
    kpiSemantic.querySelector('.kpi-value span').textContent = (totalFiles ? Math.round(((totalFiles - keywordOnly) / totalFiles) * 100) : 0).toLocaleString();

    folderChart.data.labels = roots.map((r) => fileName(r.path) || r.path);
    folderChart.data.datasets[0].data = roots.map((r) => r.files);
    folderChart.update('none');

    if (files) { typeChart?.destroy(); drawTypes(files); }
    drawOrganize(batches); // cheap (<=20 bars): simplest to redraw rather than diff two stacked datasets
  };
}
