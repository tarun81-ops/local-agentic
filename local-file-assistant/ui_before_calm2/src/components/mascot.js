import openUrl from '../assets/lumi-open.png';
import closedUrl from '../assets/lumi-closed.png';

/**
 * Lumi, the app mascot: your anime art in a square tile that matches the app's hard-edged style, animated with CSS
 * (floating, blinking via a second "eyes closed" frame, glow, sparkles).
 * mood: 'idle' | 'thinking' | 'listening' | 'worried' - change later with setMood(node, mood).
 * flat: no gradient glow (used in the sidebar).
 */
// Pause every CSS animation while the window is hidden (html.paused, see style.css). Runs once per page.
if (typeof document !== 'undefined' && !document.documentElement.dataset.lumiVis) {
  document.documentElement.dataset.lumiVis = '1';
  const sync = () => document.documentElement.classList.toggle('paused', document.hidden);
  document.addEventListener('visibilitychange', sync);
  sync();
}

export function mascot({ size = 32, mood = 'idle', label = '', flat = false } = {}) {
  const wrap = document.createElement('span');
  wrap.className = `mascot${size <= 44 ? ' is-small' : ''}${flat ? ' is-flat' : ''}`;
  wrap.dataset.mood = mood;
  wrap.style.setProperty('--m-size', `${size}px`);
  if (label) {
    wrap.setAttribute('role', 'img');
    wrap.setAttribute('aria-label', label);
  } else wrap.setAttribute('aria-hidden', 'true');
  wrap.innerHTML = `<span class="m-tile"><span class="m-glow"></span><span class="m-figure"><img class="m-open" src="${openUrl}" alt="" draggable="false"><img class="m-closed" src="${closedUrl}" alt="" draggable="false"></span><i class="m-spark"></i><i class="m-spark"></i><i class="m-spark"></i><span class="m-corner"></span></span>`;
  return wrap;
}

export function setMood(node, mood) {
  if (node) node.dataset.mood = mood;
}
