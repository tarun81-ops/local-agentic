"""WCAG contrast for every theme x accent, computed from the real tokens in ui/src/style.css.
Small text needs 4.5:1; UI colours (indicators, focus rings, borders) need 3:1."""
import itertools
import re
from pathlib import Path

import pytest

CSS = re.sub(r"/\*.*?\*/", "", (Path(__file__).resolve().parents[2] / "ui" / "src" / "style.css").read_text(encoding="utf-8"), flags=re.S)
ACCENTS = ("red", "blue", "green", "violet")
SYSTEM_DARK = ':root:not([data-theme="light"]):not([data-theme="dark"])'


def _rules(text: str) -> dict[str, dict[str, str]]:
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        decls = dict(re.findall(r"--([\w-]+):\s*([^;]+);", body))
        if decls:
            out[" ".join(sel.split())] = decls
    return out


def _media_dark() -> str:
    start = CSS.index("@media (prefers-color-scheme: dark) {")
    depth, i = 0, CSS.index("{", start)
    for j in range(i, len(CSS)):
        depth += {"{": 1, "}": -1}.get(CSS[j], 0)
        if depth == 0:
            return CSS[i + 1 : j]
    raise AssertionError("unclosed media block")


MEDIA = _media_dark()
RULES = _rules(CSS.replace(MEDIA, ""))
DARK_RULES = _rules(MEDIA)


def tokens(theme: str, accent: str) -> dict[str, str]:
    if theme == "light":
        parts = [RULES[':root, :root[data-theme="light"]']] + ([RULES[f':root[data-accent="{accent}"]']] if accent != "red" else [])
    elif theme == "dark":
        parts = [RULES[':root[data-theme="dark"]']] + ([RULES[f':root[data-theme="dark"][data-accent="{accent}"]']] if accent != "red" else [])
    else:  # "system" while the OS is dark (the OS light case is the light theme)
        parts = [DARK_RULES[SYSTEM_DARK]] + ([DARK_RULES[f'{SYSTEM_DARK}[data-accent="{accent}"]']] if accent != "red" else [])
    merged: dict[str, str] = {}
    for p in parts:
        merged.update(p)
    return {k: v.strip() for k, v in merged.items()}


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


SURFACES = ("paper", "paper-alt")
# (foreground, background) pairs that really occur in style.css, with the minimum ratio each needs.
SMALL_TEXT = [(fg, bg) for fg in ("ink", "ink-dim", "ink-faint", "red-text") for bg in SURFACES] + [
    ("ink", "paper-deep"), ("red-text", "paper-deep"),
    ("on-color", "red"), ("on-color", "red-deep"), ("on-color", "red-strong"), ("on-color", "blue"),
    ("paper", "ink"), ("ink", "red-tint"), ("ink", "blue-tint"), ("ink-dim", "blue-tint"), ("on-yellow", "yellow"),
]
UI = [(fg, bg) for fg in ("red", "blue") for bg in SURFACES]


@pytest.mark.parametrize("theme,accent", list(itertools.product(("light", "dark", "system"), ACCENTS)))
def test_every_theme_and_accent_passes(theme, accent):
    t = tokens(theme, accent)
    failures = [f"{fg} on {bg}: {ratio(t[fg], t[bg]):.2f} < 4.5" for fg, bg in SMALL_TEXT if ratio(t[fg], t[bg]) < 4.5]
    failures += [f"{fg} vs {bg}: {ratio(t[fg], t[bg]):.2f} < 3" for fg, bg in UI if ratio(t[fg], t[bg]) < 3.0]
    assert not failures, f"{theme}/{accent}: " + "; ".join(failures)


def test_system_dark_matches_the_dark_theme():
    for accent in ACCENTS:
        assert tokens("system", accent) == tokens("dark", accent)


def test_no_hard_coded_colours_outside_the_token_blocks():
    body = re.sub(r"@media \(prefers-color-scheme: dark\) \{.*?\n\}\n", "", CSS, flags=re.S)
    body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith((":root", "@media")))
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", body)
