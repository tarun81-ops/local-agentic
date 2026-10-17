# Local File Assistant: UI design audit

Date: 2026-10-05. Scope: `ui/src/style.css` (443 lines), `appearance.js`, `pages/*`, `components/*`, plus the new Lumi mascot work.

Method: static analysis of the CSS and JS, plus contrast ratios computed from the tokens. The UI was **not rendered** for this audit, so the Responsive and Polish scores are inferred from code. Run `npm run dev` and eyeball the items marked "verify".

## Score: 71 / 100

| # | Dimension | Score | One-line reason |
|---|-----------|-------|-----------------|
| 1 | Color consistency | 8 | Fully tokenised, accent-swappable, contrast-tested. Mascot adds hard-coded colours. |
| 2 | Typography hierarchy | 6 | Hierarchy is clear, but there are 18 distinct font sizes, many only 0.5px apart. |
| 3 | Spacing rhythm | 6 | 25 distinct spacing values, 15 of them off a 4px grid. |
| 4 | Component consistency | 7 | Shared `.btn/.badge/.box/.field`. The rounded mascot tile breaks the hard-edge language. |
| 5 | Responsive behavior | 6 | Fixed 264px sidebar, wrap rules only, no width breakpoints (verify at narrow window sizes). |
| 6 | Dark mode | 9 | Complete token set, system/light/dark, all four accents defined in both themes. |
| 7 | Animation | 6 | Core motion is purposeful. The mascot adds many always-on loops and a forced splash delay. |
| 8 | Accessibility | 8 | AA-tested tokens, forced-colors and reduced-motion handled. One failing pair, three `outline: none`. |
| 9 | Information density | 7 | Clean, but 21 rules use 11px mono captions. |
| 10 | Polish | 8 | Hover/active states, empty states, loading states all present. Splash adds more. |

## What is already strong (keep)

- A distinct design language: paper/ink, one accent, hard edges, 2px rules, hard offset shadows (`4px 4px 0 var(--ink)`). Not generic.
- Tokens at `style.css:3-16`, with the accent swappable through `data-accent`. Every pair is checked by `tests/test_contrast.py` per the comment at line 2.
- Archivo plus Space Mono has personality (not a default sans stack).
- Motion that carries meaning: `.nav-indicator` slide (line 53), `bubbleIn` (155), streaming caret (147), and the `.progress` bar (241).

## Findings, by dimension (with fixes)

### 1. Color consistency: 8

Colours outside the token block (excluding lines 1-16): `#000` x2, `rgba(0,0,0,.45)`, and the mascot block's `#14120F`, `#E63312`, `#FFE79A`, `#fff`, `rgba(236,72,130,.55)`, `rgba(90,170,230,.45)`.

- **`style.css:408` `.m-corner { background: #E63312 }`** does not follow `data-accent`. On the blue, green or violet accents the mascot corner stays red, and it differs from the token red `#D93312` even on the default accent. Fix: `background: var(--red);`.
- `style.css:402` `.m-tile { background: #14120F }` and `:403` the pink/blue glow are intentionally fixed, because the art needs a dark backdrop in both themes. Keep, but add a comment saying so, since they bypass `test_contrast.py`.

### 2. Typography hierarchy: 6

Distinct `font-size` values: 8, 9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 16, 17, 24, 26. The hierarchy reads fine (page title 24/900, section heads 12.5/700, body 13-14.5, mono captions), but the half-pixel steps mean nothing is really on a scale.

Fix: collapse to a scale, for example `--fs-xs: 11px; --fs-sm: 12px; --fs-md: 13px; --fs-lg: 14px; --fs-xl: 16px; --fs-h: 24px; --fs-kpi: 26px;`, then map 9-10.5 to 11, 11.5 to 12, 12.5 to 13, 13.5 and 14.5 to 14. Sizes 8-10px are below comfortable reading size, so verify the `.swatch` labels and `.tag-local`.

### 3. Spacing rhythm: 6

Most-used values: 8 (24x), 10 (24x), 12 (23x), 18 (22x), 14 (20x), 4 and 16 (17x each). Off a 4px grid: 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 18, 22, 26, 30.

Fix: adopt `--s-1: 4px, --s-2: 8px, --s-3: 12px, --s-4: 16px, --s-5: 24px, --s-6: 32px`. Cheap wins: 10 to 8 or 12, 14 to 12 or 16, 18 to 16, 22 to 24, 26 to 24, 30 to 32. Do this per component, not as a global search-and-replace.

### 4. Component consistency: 7

- **The mascot breaks the "hard edges" rule.** `style.css:402` gives `.m-tile` a rounded radius (20% of its size), `:416` the listening ring is rounded, and `:431` adds a soft drop-shadow. Every other surface in the app is square with 1.5-2px ink borders. Choose one:
  - **A (matches the system):** square tile with a border, `border-radius: 0; border: 1.5px solid var(--ink);` on `.m-tile` and `.mascot[data-mood="listening"]::after`, and a hard shadow `box-shadow: 3px 3px 0 var(--ink)` instead of the blurred drop-shadow on `.splash .mascot`. The `.ico` and tray icon can stay rounded, since they are OS icons.
  - **B (keep it soft):** keep the rounded tile on purpose, as the one organic element against the grid. The app icon already matches this.
- **Dead CSS from the old logo.** Lines 38-42 (`.mark`, `@keyframes breathe`, `.mark div`, `.mark-lg`), line 90 (`.boot .mark`) and line 296 (`.mark-overlay div`) are no longer used by any JS. Delete them. Keep `.mark-overlay { width/height }` (line 295) and the `.mark-overlay` selector in the compact rule (line 389), which still applies to the mascot slot.

### 5. Responsive behavior: 6 (not rendered, so verify)

Only wrap rules exist (for example `.page-head { flex-wrap: wrap }`). The sidebar is a fixed 264px (line 34) with no collapse. Electron windows can be resized, so check the main window at its minimum width. Fix if needed: below about 760px, collapse the sidebar to icons or put it in a slide-over.

### 6. Dark mode: 9

Complete: light, dark, system, plus accents for each. Nit: the same dark token set is duplicated at lines 4 and 6, so a change needs two edits and the copies can drift.

### 7. Animation: 6

Counts: 16 keyframes, 12 `infinite` animations.

- **Sidebar mascot never rests.** Float, sway, glow and blink (`style.css:404`, `:403`, `.m-closed`) run forever at 34px, where most of the motion is barely visible. Fix: at small sizes keep only the blink, and move float/glow to `:hover` or to `data-mood` changes. Example: `.mascot.is-small .m-figure, .mascot.is-small .m-glow { animation: none; }`.
- **Forced splash delay.** `main.js` `boot()` waits at least 1.6s on every launch, even when the backend is already up. Fix: show the intro only on the first launch of a session, or cap it at about 900ms.
- **Idle cost.** Always-on animations keep painting while the window is in the background. Fix: a `visibilitychange` listener that toggles a class with `animation-play-state: paused` on `#app`.
- Reduced motion is handled globally at line 29, plus an override at line 442 so the splash text does not stay invisible. Good.

### 8. Accessibility: 8

Computed contrast, light / dark:

| Pair | Light | Dark | AA (4.5 small text) |
|------|-------|------|------|
| ink-dim on paper | 6.48 | 9.80 | pass |
| ink-faint on paper | 5.30 | 7.37 | pass |
| ink-faint on paper-alt | 4.72 | 6.84 | pass |
| **ink-faint on paper-deep** | **4.01** | 5.93 | **fails in light** |
| red-text on red-tint | 5.42 | 4.63 | pass |
| white on red (badge, cite, send) | 4.74 | 4.74 | pass |
| white on red-deep (primary button) | 5.75 | 5.75 | pass |
| white on blue (LOCAL tag) | 7.68 | 4.72 | pass |

- **`style.css:55` and `:67`** (`.nav-item:hover`, `.recent-item:hover` set `background: var(--paper-deep)`) put `.nav-n` text (ink-faint, 11px) on paper-deep, which is 4.01:1 in the light theme. Fix: darken light `--ink-faint` slightly (about `#5F5A50`), or use `--paper-alt` for the hover background. Re-run `tests/test_contrast.py` after.
- **`outline: none` at lines 180, 189, 297** (`.composer input`, `.search-bar input`, `#query`). The composer and search bar compensate with `:focus-within` shadows (lines 179, 188). Verify `#query` in the overlay shows a visible focus indicator.
- Mascot: `aria-hidden` in the sidebar, overlay and chat rows; the splash mascot has a label, and the title has an `aria-label`. Good.

### 9. Information density: 7

Clean layout and generous padding, but 21 rules use 11px mono text, which makes captions tiring at length. Fix: move captions to the 12px step when you apply the type scale in item 2.

### 10. Polish: 8

Hover, active and focus states exist. Empty states use a dashed box (`.empty`, line 123). Loading uses `.dots`, `.progress`, and the splash. Gap: if the backend fails to start, the mascot disappears and a plain error notice replaces it (`main.js`). Optional: keep the mascot above that notice with the closed-eyes frame held, as a "worried" state.

## AI-slop check

Low risk overall. No glassmorphism, no purple-to-blue default gradients, no scroll-triggered animation, and the font stack has character.

Watch items, all from the mascot block:

- The pink-and-blue radial glow behind the character (`style.css:403`) is the nearest thing to a generic AI gradient. It is justified by the art's palette, but keep it subtle.
- Three twinkling sparkles (`.m-spark`, line 409) are decoration with no meaning in the large splash. They are hidden at small sizes (line 413), which is correct.
- Rounded tile and soft shadow: see item 4.

## Suggested order of work (about 1-2 hours)

1. Fix `.m-corner` to `var(--red)` (1 line).
2. Delete the dead `.mark` CSS (about 9 lines).
3. Pause float/glow at small mascot sizes; cap the splash delay (about 5 lines).
4. Fix the ink-faint on paper-deep contrast and re-run the contrast test.
5. Decide mascot corners: A (square, matches the system) or B (keep rounded).
6. Introduce the type and spacing scales as CSS variables, then migrate one page at a time.

## Applied on 2026-10-05

- Mascot corner now uses `var(--red)` and follows the accent colour.
- Tile is square with a 1.5px ink border; splash uses a hard 6px offset shadow instead of the blurred drop-shadow; the listening ring is square.
- Dead logo CSS removed (`.mark`, `breathe`, `.mark-lg`, `.boot .mark`, `.mark-overlay div`).
- Small mascots (sidebar, chat, overlay) only blink; float/glow/sparkles stay for the splash and the thinking state. Sidebar mascot has no gradient glow (`flat`).
- Animations pause while the window is hidden (`html.paused`).
- Splash shortened to about 0.9s (hold and title timing); burst is quieter (opacity .12, thinner rays).
- Backend-failure screen now shows the mascot in a "worried" state (closed eyes, no motion) above the error.
- Chat thinking mascot enlarged from 24px to 28px.
- Contrast: `.nav-item:hover .nav-n` uses `ink-dim` (4.9:1) instead of `ink-faint` (4.0:1) on the hover background.
- Overlay search box shows a blue underline on focus.
- Type scale tokens (`--fs-*`) and spacing tokens (`--s-*`) added. Half-pixel font sizes snapped to whole steps and all whole sizes from 11 to 26px (except 17px) now use the tokens. Sizes 8-10px and 17px stay literal, and spacing is not migrated yet (do it per component).

Not done: the duplicated dark token block (lines 4 and 6) and migrating existing spacing values.
