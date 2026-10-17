"""Fuzzy file-name matching, shared by Find, Search, Chat and the whole-PC catalog.

People type titles by sound, not by spelling: "gunaho ke devata" for "Gunahon Ka Devta.pdf", "jindagi"
for "Zindagi", "geeta" for "Gita". Exact and prefix matching misses all of those. This module is the one
place that decides "do these two words sound alike", so every part of the app agrees.

How it works (no model, no third-party package):
  * fold(word)    - a phonetic spelling: lower-case, accents removed, Devanagari turned into Latin,
                    Hinglish variants collapsed (w->v, ph->f, aa->a, ee->i, "dh"->"d", doubled letters).
  * word_sim(a,b) - 0..1 similarity of two folded words (numbers must match exactly, short words too).
  * name_match()  - how well a file name covers the words someone typed, with the words that hit.
  * Vocab         - a trigram index over the words that appear in file names, so a typed word can be
                    expanded to the real spellings in a folder or catalog in microseconds, without
                    comparing it with every file.
  * name_similarity() - how alike two file names are (used for "similar files")."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

CUTOFF = 0.8  # two words count as the same word at or above this similarity

# Glue words that carry no information about which file is meant ("gunaho ke devata": "ke" is noise).
PARTICLES = frozenset(
    "ke ka ki ko ne se me mein par pe hai ho hain tha thi the aur ya ek of the a an and in on to for by with from at".split()
)
# Words around a title in a chat message, not part of it ("summarise the book gunahon ka devta").
_COMMAND_WORDS = frozenset(
    """summarise summarize summary open read explain tell show find search give get please plz about on me us
    book novel story kitab pustak file document doc pdf docx notes chapter what who which is are was
    can could would you i my mera meri mujhe batao bata dikhao kholo padho""".split()
)

_TOKEN = re.compile(r"(?:[^\W_]|[ऀ-ॿ])+")  # letters, digits and Devanagari (its vowel signs are not \w)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ---- Devanagari -> rough Latin --------------------------------------------------------------

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
_MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e"}
_VIRAMA, _NUKTA = "्", "़"


def transliterate(text: str) -> str:
    """Devanagari -> Latin ("गुनाहों का देवता" -> "gunaahon kaa devataa"); other characters pass through."""
    text = unicodedata.normalize("NFC", text)
    out: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in _CONS:
            j = i + 1
            if j < n and text[j] == _NUKTA:
                j += 1
            base = _CONS[ch]
            if j < n and text[j] == _VIRAMA:  # conjunct: no vowel
                out.append(base)
                i = j + 1
            elif j < n and text[j] in _MATRAS:
                out.append(base + _MATRAS[text[j]])
                i = j + 1
            else:  # inherent "a", dropped at the end of a word (schwa deletion)
                at_end = j >= n or not (_DEVANAGARI.match(text[j]))
                out.append(base if at_end else base + "a")
                i = j
            continue
        if ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in "ंँ":
            out.append("n")
        elif ch == "ः":
            out.append("h")
        elif "ऀ" <= ch <= "ॿ":
            pass  # danda, digits, other marks
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---- folding and similarity ---------------------------------------------------------------

@lru_cache(maxsize=131072)
def fold(word: str) -> str:
    """A phonetic spelling of one word, so spelling variants of the same sound compare equal."""
    w = word.lower()
    if _DEVANAGARI.search(w):
        w = transliterate(w)
    w = unicodedata.normalize("NFKD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    w = re.sub(r"[^\w]|_", "", w)
    w = w.replace("ph", "f").replace("ck", "k").replace("w", "v").replace("z", "j").replace("q", "k")
    w = re.sub(r"([bcdgjkpst])h+", r"\1", w)  # aspirates: dh, bh, kh, th, sh, ch ... lose the h
    w = w.replace("ee", "i").replace("oo", "u").replace("aa", "a").replace("ii", "i").replace("uu", "u")
    w = w.replace("ai", "e").replace("ay", "e").replace("au", "o").replace("ou", "o")
    return re.sub(r"(.)\1+", r"\1", w)  # doubled letters


@lru_cache(maxsize=262144)
def _sim_folded(fa: str, fb: str) -> float:
    if fa == fb:
        return 1.0
    if not fa or not fb:
        return 0.0
    if any(c.isdigit() for c in fa) or any(c.isdigit() for c in fb):
        return 0.0  # numbers and years must match exactly
    short, long_ = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    if len(short) < 3:
        return 0.0
    if long_.startswith(short) and (len(short) >= 4 or len(long_) - len(short) <= 1):
        return 0.9  # a typed prefix, or one letter of plural/suffix
    if len(short) < 4:
        return 0.0
    sm = SequenceMatcher(None, fa, fb, autojunk=False)
    if sm.real_quick_ratio() < CUTOFF - 0.05 or sm.quick_ratio() < CUTOFF - 0.05:
        return 0.0
    return sm.ratio()


def word_sim(a: str, b: str) -> float:
    """0..1 how alike two words sound; 1.0 for spelling variants of the same word."""
    return _sim_folded(fold(a), fold(b))


def split_name(text: str) -> list[str]:
    """'Electricity_Bill-Sep2026' / 'GunahonKaDevta' -> lower-case words (CamelCase and digits split)."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    return [t.lower() for t in _TOKEN.findall(text)]


def _stem_of(name: str) -> str:
    """A file name without its extension; a folder name or a bare word is returned as it is."""
    p = Path(name)
    return p.stem if p.suffix and 1 < len(p.suffix) <= 6 and not p.suffix[1:].isdigit() else p.name


PARTIAL_CUTOFF = 0.88  # stricter than CUTOFF: a word found *inside* a longer word must be a close match


def _partial(fw: str, one_word: str) -> float:
    """The best match of a folded word inside ONE long folded word ('gunahonkadevta'): names written
    without separators still match. Never used across word boundaries ("letter" must not match the
    middle of "electricity bill")."""
    joined = one_word
    n = len(fw)
    if n < 5 or len(joined) < n + 3 or len(joined) > 80:
        return 0.0
    best = 0.0
    for size in (n - 1, n, n + 1):
        for i in range(0, max(1, len(joined) - size + 1)):
            window = joined[i : i + size]
            if window[:1] != fw[:1] and window[1:2] != fw[1:2]:
                continue  # cheap gate: a window must start like the word (give or take a letter)
            best = max(best, _sim_folded(fw, window))
            if best >= 0.99:
                return best
    return best


def name_match(words: list[str], name: str) -> tuple[float, list[tuple[str, str, float]]]:
    """How well a file name covers the typed words: (score 0..1, [(typed word, name word, similarity)]).
    Glue words ("ke", "ka", "the") are ignored; the score is the average over the remaining words, so a
    name that matches every word scores about 1.0 and one that matches half scores about 0.5."""
    want = [w for w in dict.fromkeys(words) if fold(w) and w.lower() not in PARTICLES]
    toks = split_name(_stem_of(name))
    folded = [fold(t) for t in toks]
    if not want or not folded:
        return 0.0, []
    total, hits = 0.0, []
    for w in want:
        fw = fold(w)
        best, best_tok = 0.0, ""
        for t, ft in zip(toks, folded):
            s = _sim_folded(fw, ft)
            if s > best:
                best, best_tok = s, t
        if best < CUTOFF:
            for t, ft in zip(toks, folded):
                s = _partial(fw, ft)
                if s >= PARTIAL_CUTOFF and s > best:
                    best, best_tok = s, t
        if best >= CUTOFF:
            total += best
            hits.append((w, best_tok, best))
    return total / len(want), hits


def name_similarity(a: str, b: str) -> float:
    """0..1 how alike two file names are, word by word with fuzzy matching (a Dice score), ignoring the
    extension: 'Electricity_Bill-Sep2026' and 'Electricity_Bill-Jun2026' are 0.75 alike."""
    ta = [t for t in split_name(_stem_of(a)) if t not in PARTICLES]
    tb = [t for t in split_name(_stem_of(b)) if t not in PARTICLES]
    if not ta or not tb:
        return 0.0
    fb = [fold(t) for t in tb]
    used: set[int] = set()
    matched = 0.0
    for t in ta:
        ft = fold(t)
        best, at = 0.0, -1
        for i, g in enumerate(fb):
            if i in used:
                continue
            s = _sim_folded(ft, g)
            if s > best:
                best, at = s, i
        if best >= CUTOFF:
            used.add(at)
            matched += 1.0
    return 2 * matched / (len(ta) + len(tb))


# ---- reading a typed message as a title --------------------------------------------------------

def title_words(message: str) -> list[str]:
    """The words of a chat message that could be a file or book title: command words ("summarise",
    "book", "pdf") and glue words ("ke", "the") dropped."""
    words = [t.lower() for t in _TOKEN.findall(message)]
    return [w for w in words if w not in PARTICLES and w not in _COMMAND_WORDS and len(w) > 1]


# ---- vocabulary index: typed word -> real spellings --------------------------------------------

def _grams(folded: str) -> set[str]:
    padded = f"^{folded}$"
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


class Vocab:
    """The words that appear in a set of file names, indexed by trigram of their folded spelling.
    close(word) returns the real spellings that sound like `word`, without scanning every word."""

    def __init__(self, tokens):
        self._raw: dict[str, set[str]] = defaultdict(set)  # folded -> real spellings
        self._grams: dict[str, set[str]] = defaultdict(set)  # trigram -> folded words
        for t in set(tokens):
            if not t or len(t) > 40:
                continue
            f = fold(t)
            if len(f) < 3 or f.isdigit():
                continue
            self._raw[f].add(t)
        for f in self._raw:
            for g in _grams(f):
                self._grams[g].add(f)

    def __len__(self) -> int:
        return len(self._raw)

    def close(self, word: str, limit: int = 6) -> list[str]:
        """Real spellings in this vocabulary that sound like `word` (the typed spelling itself excluded)."""
        fw = fold(word)
        if len(fw) < 4 or any(c.isdigit() for c in fw):
            return []
        counts: Counter = Counter()
        for g in _grams(fw):
            for f in self._grams.get(g, ()):
                counts[f] += 1
        need = 2 if len(fw) >= 5 else 1
        scored = []
        for f, c in counts.most_common(300):
            if c < need:
                break
            s = _sim_folded(fw, f)
            if s >= CUTOFF:
                scored.append((s, f))
        scored.sort(reverse=True)
        out: list[str] = []
        for _, f in scored[:limit]:
            out += sorted(self._raw[f])
        return [t for t in dict.fromkeys(out) if t != word.lower()]
