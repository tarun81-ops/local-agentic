"""Speech to text with faster-whisper on the CPU (int8). The input is raw 16 kHz mono PCM16,
which is what the app's microphone code produces, so no audio codec is needed here."""
import numpy as np

from app.config import settings
from app.core import prefs
from app.core.assistant.voice import Lazy, VoiceUnavailable

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3  # shorter than this is a click or a bump, not speech
MAX_SECONDS = 240

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
    model = _model.get(_load)
    segments, _ = model.transcribe(audio, language=language or prefs.get("voice_language") or None, beam_size=1, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    _model.touch()
    return text


def download() -> str:
    """Fetches the model once into models_dir (setup-time, needs internet)."""
    from faster_whisper.utils import download_model

    return download_model(model_name(), output_dir=str(model_dir()))


if __name__ == "__main__":
    print("ready:", download())
