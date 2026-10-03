"""Text to speech with Piper, in-process through its Python API (a frozen build has no python.exe
to launch). A voice is two files in models_dir/piper: NAME.onnx and NAME.onnx.json."""
import io
import re
import wave

import httpx

from app.config import settings
from app.core import prefs
from app.core.assistant.voice import Lazy, VoiceUnavailable

MAX_CHARS = 2000
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

_voice = Lazy()
# "(report.pdf, page 2)": useful on screen, noise when read aloud.
_CITATION = re.compile(r"\s*\((?:[^()]*?,\s*)?(?:page|part|slide|sheet|row|line)\s*\d+[^()]*\)", re.I)
_MARKUP = re.compile(r"[*_`#>]+")


def voice_name() -> str:
    return prefs.get("voice_tts_voice") or "en_US-lessac-medium"


def _paths(name: str | None = None):
    base = settings.voice_models_dir / "piper"
    name = name or voice_name()
    return base / f"{name}.onnx", base / f"{name}.onnx.json"


def available() -> bool:
    return all(p.is_file() for p in _paths())


def clean_for_speech(text: str) -> str:
    text = _MARKUP.sub("", _CITATION.sub("", text))
    return " ".join(text.split())[:MAX_CHARS]


def _load():
    from piper import PiperVoice

    onnx, config = _paths()
    return PiperVoice.load(str(onnx), config_path=str(config))


def synthesize(text: str) -> bytes:
    """A WAV file of the text read aloud."""
    if not available():
        raise VoiceUnavailable("The voice isn't downloaded yet. Use Settings > Voice > Download voice models.")
    spoken = clean_for_speech(text)
    if not spoken:
        raise ValueError("There is nothing to read aloud.")
    voice = _voice.get(_load)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        voice.synthesize_wav(spoken, wav)
    _voice.touch()
    return buf.getvalue()


def download() -> str:
    """Fetches the voice once (setup-time, needs internet). NAME looks like en_US-lessac-medium."""
    name = voice_name()
    locale, speaker, quality = name.split("-")
    folder = f"{locale.split('_')[0]}/{locale}/{speaker}/{quality}"
    for dest in _paths(name):
        if dest.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with httpx.stream("GET", f"{VOICES_URL}/{folder}/{dest.name}", follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for block in r.iter_bytes(1 << 20):
                    f.write(block)
        part.replace(dest)  # a half-finished download never looks like a voice
    return str(_paths(name)[0])


if __name__ == "__main__":
    print("ready:", download())
