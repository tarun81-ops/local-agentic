import struct

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.assistant import voice
from app.core.assistant.voice import VoiceUnavailable, stt, tts
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def models(tmp_path, monkeypatch):
    """An empty models dir; tests add the files they need."""
    monkeypatch.setattr(settings, "voice_models_dir", tmp_path)
    monkeypatch.setattr(stt, "_model", voice.Lazy())
    monkeypatch.setattr(tts, "_voice", voice.Lazy())
    return tmp_path


def _have_stt(models):
    d = models / "whisper" / "base"
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")


def _have_tts(models):
    d = models / "piper"
    d.mkdir(parents=True)
    (d / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (d / "en_US-lessac-medium.onnx.json").write_text("{}")


class FakeWhisper:
    def __init__(self):
        self.heard = None

    def transcribe(self, audio, language=None, beam_size=1, vad_filter=True):
        self.heard = (audio, language)

        class Seg:
            def __init__(self, t):
                self.text = t

        return [Seg(" hello "), Seg("world ")], None


def pcm(seconds, value=1000):
    return struct.pack("<h", value) * int(16000 * seconds)


def test_stt_converts_pcm_and_joins_segments(models, monkeypatch):
    _have_stt(models)
    fake = FakeWhisper()
    monkeypatch.setattr(stt, "_load", lambda: fake)
    assert stt.transcribe(pcm(1), "en") == "hello world"
    audio, lang = fake.heard
    assert lang == "en" and len(audio) == 16000 and audio.dtype.name == "float32" and abs(audio[0] - 1000 / 32768) < 1e-6


def test_stt_refuses_without_a_model_and_ignores_clicks_and_overlong_audio(models, monkeypatch):
    with pytest.raises(VoiceUnavailable, match="downloaded"):
        stt.transcribe(pcm(1))
    _have_stt(models)
    monkeypatch.setattr(stt, "_load", lambda: pytest.fail("model must not load for a click"))
    assert stt.transcribe(pcm(0.1)) == "" and stt.transcribe(b"") == ""
    with pytest.raises(VoiceUnavailable, match="too long"):
        stt.transcribe(pcm(stt.MAX_SECONDS + 1, 0))


def test_models_are_not_loaded_when_ram_is_low_and_are_dropped_after_idle(models, monkeypatch):
    _have_stt(models)
    loads = []
    monkeypatch.setattr(stt, "_load", lambda: loads.append(1) or FakeWhisper())
    monkeypatch.setattr(voice.ram, "is_low", lambda: True)
    with pytest.raises(VoiceUnavailable, match="memory"):
        stt.transcribe(pcm(1))
    assert not loads
    monkeypatch.setattr(voice.ram, "is_low", lambda: False)
    stt.transcribe(pcm(1))
    stt.transcribe(pcm(1))
    assert len(loads) == 1 and stt._model.loaded()  # kept between uses
    monkeypatch.setattr(settings, "llm_idle_unload_s", 600)
    assert not stt._model.drop_if_idle(stt._model._last + 599) and stt._model.drop_if_idle(stt._model._last + 600)
    stt.transcribe(pcm(1))
    assert len(loads) == 2  # reloaded on demand
    monkeypatch.setattr(settings, "llm_idle_unload_s", 0)
    assert not stt._model.drop_if_idle(stt._model._last + 10**9)  # 0 = never


def test_speech_text_drops_citations_and_markup():
    spoken = tts.clean_for_speech("**The total** is $4,250 (invoice.pdf, page 1). See `notes` (report.docx, part 2).\n\n# Done")
    assert spoken == "The total is $4,250. See notes. Done"
    assert len(tts.clean_for_speech("word " * 1000)) <= tts.MAX_CHARS


def test_tts_writes_a_wav_and_needs_a_voice(models, monkeypatch):
    with pytest.raises(VoiceUnavailable, match="downloaded"):
        tts.synthesize("hello")
    _have_tts(models)
    said = []

    class FakeVoice:
        def synthesize_wav(self, text, wav):
            said.append(text)
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\0\0" * 100)

    monkeypatch.setattr(tts, "_load", lambda: FakeVoice())
    wav = tts.synthesize("Total (a.pdf, page 1) is **4**")
    assert wav[:4] == b"RIFF" and said == ["Total is 4"]
    with pytest.raises(ValueError):
        tts.synthesize("(a.pdf, page 1)")  # nothing left to say


def test_endpoints(models, monkeypatch):
    _have_stt(models)
    monkeypatch.setattr(stt, "_load", lambda: FakeWhisper())
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        s = c.get("/voice/status", headers=AUTH).json()
        assert s == {"stt": {"available": True, "model": "base"}, "tts": {"available": False, "voice": "en_US-lessac-medium"}}
        r = c.post("/voice/stt", content=pcm(1), headers={**AUTH, "Content-Type": "application/octet-stream"})
        assert r.json() == {"text": "hello world"}
        assert c.post("/voice/tts", json={"text": "hi"}, headers=AUTH).status_code == 503  # no voice yet
        too_big = c.post("/voice/stt", content=b"\0" * (stt.SAMPLE_RATE * 2 * stt.MAX_SECONDS + 2), headers=AUTH)
        assert too_big.status_code == 413
        assert c.post("/voice/stt", headers=AUTH).json() == {"text": ""}  # an empty recording
        assert c.get("/voice/status").status_code == 401


def test_download_reports_each_model_separately(models, monkeypatch):
    monkeypatch.setattr(stt, "download", lambda: _have_stt(models) or "ok")
    monkeypatch.setattr(tts, "download", lambda: (_ for _ in ()).throw(OSError("offline")))
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        r = c.post("/voice/download", headers=AUTH).json()
    assert r["stt"]["available"] and not r["tts"]["available"] and r["errors"] == {"tts": "offline"}
