"""Plain text (.txt .md .csv), web pages (.html .htm) and saved emails (.eml), with the
standard library only. None of them has page numbers, so chunks are cited as parts, like
Word files."""
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from app.core.chunking import Chunk, chunk_parts

# ponytail: big text files are usually logs or data dumps rather than documents, so only the
# start is indexed. Raise this if people need to search deep inside large text files.
MAX_TEXT_BYTES = 2 * 1024 * 1024


def _read(path: Path) -> str:
    with open(path, "rb") as f:
        data = f.read(MAX_TEXT_BYTES)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if exc.start >= len(data) - 3:  # the size cap cut a multi-byte character in half
            return data[: exc.start].decode("utf-8-sig")
        return data.decode("cp1252", errors="replace")  # what older Windows editors saved


class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        self._skipping += tag in self.SKIP

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if not self._skipping and data.strip():
            self.parts.append(data.strip())


def html_text(html: str) -> str:
    p = _HtmlText()
    p.feed(html)
    p.close()
    return "\n".join(p.parts)


def parse_text(path: Path) -> list[Chunk]:
    return chunk_parts(_read(path))


def parse_html(path: Path) -> list[Chunk]:
    return chunk_parts(html_text(_read(path)))


def parse_email(path: Path) -> list[Chunk]:
    """Subject, sender, recipients and date, then the body (plain text preferred). Attachments
    are not read."""
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    head = "\n".join(f"{h}: {msg[h]}" for h in ("Subject", "From", "To", "Date") if msg[h])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = html_text(text)
    return chunk_parts(f"{head}\n\n{text}")
