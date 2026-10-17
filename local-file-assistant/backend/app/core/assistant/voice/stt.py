"""Speech to text with faster-whisper on the CPU (int8). The input is raw 16 kHz mono PCM16,
which is what the app's microphone code produces, so no audio codec is needed here."""
import numpy as np

from app.config import settings
from app.core import prefs
from app.core.assistant.voice import Lazy, VoiceUnavailable

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3  # shorter than this is a click or a bump, not speech
MAX_SECONDS = 240
# Words this app hears a lot; Whisper uses them as a spelling hint, not as text to output.
HINT = "Local File Assistant. Find my file, open the PDF, invoice, resume, marksheet, Downloads folder, remind me, summarize."

_model = Lazy()


def model_name() -> str:
    return prefs.get("voice_stt_model") or "base"


def model_dir():
    return settings.voice_models_dir / "whisper" / model_name()


def available() -> bool:
    return (model_dir() / "model.bin").is_file()


def _load():
    from faster_whisper import WhisperModel

    return WhisperModel(str(model_dir()), device="cpu", compute_type="int8", local_files_only=True)


def _language(language: str | None) -> str | None:
    """English unless told otherwise. Guessing the language on a few seconds of Hinglish went badly
    (logs showed 'ru' at 17%), and English letters are how Hinglish is typed anyway. "auto" = guess."""
    lang = language or prefs.get("voice_language") or "en"
    return None if lang == "auto" else lang


def transcribe(pcm: bytes, language: str | None = None) -> str:
    """Text for a recording; "" for silence or a recording too short to hold speech."""
    if not available():
        raise VoiceUnavailable("The speech model isn't downloaded yet. Use Settings > Voice > Download voice models.")
    samples = len(pcm) // 2
    if samples < SAMPLE_RATE * MIN_SECONDS:
        return ""
    if samples > SAMPLE_RATE * MAX_SECONDS:
        raise VoiceUnavailable(f"That recording is too long (the limit is {MAX_SECONDS // 60} minutes).")
    audio = np.frombuffer(pcm[: samples * 2], dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if 0 < peak < 0.25:  # a quiet laptop mic: bring speech up to a normal level (at most 20x)
        audio = audio * min(0.5 / peak, 20.0)
    model = _model.get(_load)
    segments, _ = model.transcribe(
        audio,
        language=_language(language),
        beam_size=5,  # 1 was fast but often picked the wrong words; 5 is still well under a second on CPU
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=HINT,
        condition_on_previous_text=False,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    _model.touch()
    return text


def download() -> str:
    """Fetches the model once into models_dir (setup-time, needs internet)."""
    from faster_whisper.utils import download_model

    return download_model(model_name(), output_dir=str(model_dir()))


if __name__ == "__main__":
    print("ready:", download())
