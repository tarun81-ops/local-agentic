import { api } from './api.js';

const KEY = 'lfa.appearance';
const DEFAULTS = { theme: 'system', accent: 'red', font_scale: 1, overlay_compact: false, overlay_position: 'top-right' };

/** Applies theme, accent and text size to the page and remembers them for the next start. */
export function applyAppearance(a) {
  const v = { ...DEFAULTS, ...a };
  const root = document.documentElement;
  if (v.theme === 'system') delete root.dataset.theme; // the stylesheet follows the OS setting
  else root.dataset.theme = v.theme;
  root.dataset.accent = v.accent;
  // ponytail: the stylesheet is in px, so text size is Chromium's zoom (whole UI), not a root font-size.
  root.style.zoom = String(v.font_scale);
  document.body?.classList.toggle('overlay-compact', Boolean(v.overlay_compact));
  try {
    localStorage.setItem(KEY, JSON.stringify(v)); // a UI convenience only: avoids a flash before the backend answers
  } catch { /* private mode or blocked storage: the backend value still applies */ }
  return v;
}

/** Synchronous: the last known look, before anything is fetched. */
export function applyCached() {
  try {
    const cached = JSON.parse(localStorage.getItem(KEY));
    if (cached) applyAppearance(cached);
  } catch { /* nothing cached yet */ }
}

/** The saved look from the backend (the source of truth). */
export async function loadAppearance() {
  try {
    applyAppearance((await api.personalize()).appearance);
  } catch { /* keep whatever is showing */ }
}
