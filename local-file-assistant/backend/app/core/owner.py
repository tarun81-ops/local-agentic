"""Whose document is it? "my class 12 roll no" must be answered from the user's own marksheet, not from the
first file called 12th.pdf, which may be a friend's or a sibling's.

The user's name comes from what they typed: "my name is Tarun Verma" / "mera naam ... hai" in chat (saved as the
owner_name setting), "remember that my name is ..." (a memory), or the About-me profile text. Nothing is guessed.

A document "names" a person through a labelled field ("Candidate Name : NITISH KHUNTE", "Name of Student",
"Name: ...") or simply by containing the name. check() says:
  "yes"     - the user's name is in the text (any order, any case, small OCR slips allowed);
  "no"      - the text names a person in a name field, and that person is not the user;
  "unknown" - no name field and no match (or no owner name known)."""
import re

from app.core import fuzzy, personalize

_NAME_WORD = r"[A-Za-z][A-Za-z.'-]{1,24}"
_NAME = rf"{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}}"

# Typed by the user. "i am ..." is left out on purpose: "i am in class 12" is not a name.
_SAYS_NAME = re.compile(
    rf"\bmy\s+(?:full\s+)?name\s+is\s+(?P<a>{_NAME})|\bmy\s+name'?s\s+(?P<c>{_NAME})"
    rf"|\bmera\s+naam\s+(?P<b>{_NAME_WORD}(?:\s+{_NAME_WORD})?)\s+(?:hai|h)\b",
    re.I,
)
# Words that end a typed name: "my name is tarun verma and i want ..." -> "tarun verma".
_STOP = {"and", "so", "but", "not", "please", "pls", "check", "find", "i", "im", "my", "the", "from", "in", "it", "is", "hai", "h",
         "you", "your", "this", "that", "which", "who", "what", "why", "how", "to", "of", "for", "with", "then", "than", "also", "only",
         "marksheet", "file", "files", "pdf", "jpg", "roll", "no", "number", "class", "aur", "ka", "ki", "ke", "se", "me", "mein",
         "ok", "okay", "now", "there", "here", "sir", "bro"}

# On documents: a label, then the name (capitalised words) up to the next label.
_FIELD = re.compile(
    r"(?i:candidate'?s?\s+name|name\s+of\s+(?:the\s+)?(?:candidate|student|applicant|holder|employee)|student'?s?\s+name|"
    r"applicant'?s?\s+name|\bname)\s*[:\-.]?\s*(?i:(?:mr|ms|miss|shri|smt|kumari|km)\.?\s+)?"
    r"(?P<n>[A-Z][A-Za-z.'-]+(?:[ \t]+[A-Z][A-Za-z.'-]+){0,4})",
)
# Labels that look like "name" but are someone else (a parent, the school) or not a person.
_NOT_THE_PERSON = re.compile(r"(?:father|mother|guardian|relative|parent|husband|school|college|institute|board|bank|branch|file|user)'?s?\s*$", re.I)
_LABEL_WORDS = {"roll", "no", "application", "number", "relative", "relative's", "father", "father's", "mother", "mother's", "gender",
                "class", "date", "dob", "school", "category", "address", "mobile", "email", "subject", "marks", "regn", "registration",
                "seat", "enrolment", "of", "s/o", "d/o"}


def _clean(name: str) -> str:
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z.'-]*", name):
        if w.lower().strip(".") in _STOP:
            break
        words.append(w.strip(".'-"))
    words = [w for w in words if len(w) > 1][:4]
    return " ".join(w.capitalize() for w in words)


def stated(message: str) -> str:
    """The name the user just gave ("my name is tarun verma" -> "Tarun Verma"), else ''."""
    m = _SAYS_NAME.search(message or "")
    if not m:
        return ""
    got = _clean(m.group("a") or m.group("b") or m.group("c") or "")
    return got if 1 <= len(got.split()) <= 4 and len(got) >= 3 else ""


def learn(message: str) -> str:
    """Saves the user's name when they state it ("my name is ..."). Returns the name saved, or ''."""
    name = stated(message)
    if name and name.lower() != get().lower():
        try:
            personalize.update({"owner_name": name})
        except Exception:  # a settings problem must never block the answer
            return ""
    return name


def get() -> str:
    """The user's own name: the owner_name setting, else 'my name is ...' in a memory or the About-me text."""
    pz = personalize.get_all()
    saved = pz.get("owner_name", "")
    if isinstance(saved, str) and saved.strip():
        return saved.strip()
    sources = [pz.get("profile", "")]
    try:
        from app.core.assistant import memory_store

        sources += [m["text"] for m in memory_store.list_all()]
    except Exception:
        pass
    for text in sources:
        got = stated(text or "")
        if got:
            return got
    return ""


def _words(s: str) -> list[str]:
    return [fuzzy.fold(w) for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 1]


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and abs(len(a) - len(b)) <= 2 and fuzzy.word_sim(a, b) >= 0.8


def named_people(text: str) -> list[str]:
    """Names in the document's name fields ("Candidate Name : NITISH KHUNTE" -> "Nitish Khunte"), first ones first."""
    flat = re.sub(r"[ \t]+", " ", text or "")
    out: list[str] = []
    for m in _FIELD.finditer(flat):
        before = flat[max(0, m.start() - 20): m.start()]
        if _NOT_THE_PERSON.search(before):
            continue
        words = []
        for w in m.group("n").split():
            if w.lower().strip(".:") in _LABEL_WORDS:
                break
            words.append(w.strip(".:"))
        name = " ".join(w.capitalize() for w in words[:4] if w)
        if len(name) >= 3 and name not in out:
            out.append(name)
    return out[:3]


def contains(text: str, owner: str) -> bool:
    """Every word of the owner's name appears in the text (any order, case, small OCR slips)."""
    want = _words(owner)
    if not want:
        return False
    have = set(_words(text or ""))
    return all(w in have or (len(w) >= 4 and any(_same(w, h) for h in have)) for w in want)


def check(text: str, owner: str | None = None) -> tuple[str, list[str]]:
    """("yes" | "no" | "unknown", names the document gives). See the module notes."""
    owner = get() if owner is None else owner
    people = named_people(text)
    if not owner:
        return "unknown", people
    if contains(text, owner):
        return "yes", people
    if people:
        return "no", people
    return "unknown", people


_MINE = re.compile(r"\b(?:my|mine|mera|meri|mere|i got|did i|do i have|am i)\b", re.I)


# Documents that belong to one person: whose they are decides the answer.
_PERSONAL_DOC = re.compile(
    r"\b(?:mark\s*sheets?|marks|result|roll\s*(?:no|number)?|percentage|percent|cgpa|sgpa|gpa|grades?|certificates?|"
    r"(?:class|std|standard)\s*\d{1,2}|\d{1,2}(?:th|st|nd|rd)|ssc|hsc|cbse|board|admit\s*card|hall\s*ticket|"
    r"aadhaa?r|pan(?:\s*card)?|passport|licen[cs]e|voter|id\s*card|application\s*(?:no|number)|registration|enrol?ment|"
    r"dob|date of birth|birth|seat\s*no|school|father'?s? name|mother'?s? name)\b",
    re.I,
)


def about_me(message: str) -> bool:
    """The question is about the user's OWN personal document ("my class 12 roll no", "meri marksheet")."""
    return bool(_MINE.search(message or "")) and bool(_PERSONAL_DOC.search(message or ""))
