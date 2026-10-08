"""The voiceforge bridge.

The contract under test is the boundary, not the synthesis. voiceforge owns
the model, the CUDA context and the watermark; LEDGER owns only "ask the
daemon, play what comes back, and never go silent if it is not there".

That last clause is the one worth tests. A voice assistant whose engine is a
service will meet a stopped service, and the failure has to be a downgrade to
piper rather than an exception in the middle of a session.
"""

import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ledger import voice


def wav_bytes(seconds=0.05, rate=22050):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class FakeDaemon(BaseHTTPRequestHandler):
    """Stands in for runtime/speak_server.py, same wire contract."""

    healthy = True
    spoken: list = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.healthy:
            return self._send(500, b'{"ok": false}', "application/json")
        body = json.dumps({"ok": True, "engine": "vits", "voice": "voice_a",
                           "sr": 22050, "watermark": True}).encode()
        self._send(200, body, "application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).spoken.append(payload)
        self._send(200, wav_bytes(), "audio/wav")


@pytest.fixture
def daemon():
    FakeDaemon.spoken = []
    FakeDaemon.healthy = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDaemon)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def player(monkeypatch):
    """Never actually make noise in a test run."""
    played = []
    monkeypatch.setattr(voice, "find_player", lambda: ["true"])
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: played.append(a) or None)
    return played


def test_it_speaks_through_the_daemon(daemon, player):
    tts = voice.VoiceForgeTTS(url=daemon)
    assert tts.detail == "voiceforge voice_a"
    tts.speak("episode succeeded, zero incidents")
    assert FakeDaemon.spoken == [{"text": "episode succeeded, zero incidents"}]
    assert player, "the returned wav was never handed to a player"


def test_a_chosen_voice_travels_with_the_request(daemon, player):
    voice.VoiceForgeTTS(url=daemon, voice="voice_b").speak("done")
    assert FakeDaemon.spoken[0]["voice"] == "voice_b"


def test_silence_is_not_a_request(daemon, player):
    voice.VoiceForgeTTS(url=daemon).speak("   ")
    assert FakeDaemon.spoken == []


def test_a_stopped_daemon_refuses_at_construction(player):
    """Fails fast where resolve_tts can still fall through, rather than
    failing later in the middle of a session."""
    with pytest.raises(RuntimeError, match="not reachable"):
        voice.VoiceForgeTTS(url="http://127.0.0.1:1")


def test_an_unhealthy_daemon_is_not_a_voice(daemon, player):
    FakeDaemon.healthy = False
    with pytest.raises(RuntimeError):
        voice.VoiceForgeTTS(url=daemon)


def test_a_daemon_that_dies_mid_session_does_not_crash_it(daemon, player):
    """The session keeps going mute. An exception here would take down a run
    whose actual work has nothing to do with audio."""
    tts = voice.VoiceForgeTTS(url=daemon)
    tts.url = "http://127.0.0.1:1"
    tts.speak("this goes nowhere")          # must not raise


def test_auto_falls_past_a_missing_daemon(monkeypatch):
    """The order is voiceforge, piper, sapi, silent. A daemon that is not
    running must cost the user piper, not their voice."""
    monkeypatch.setenv("LEDGER_VOICEFORGE_URL", "http://127.0.0.1:1")

    class FakePiper:
        id = "piper"

        def __init__(self):
            pass

        def speak(self, text):
            pass

    monkeypatch.setattr(voice, "PiperTTS", FakePiper)
    assert voice.resolve_tts("auto").id == "piper"


def test_asking_for_voiceforge_by_name_does_not_silently_downgrade(monkeypatch):
    """`--tts voiceforge` is a choice about provenance and watermarking. If it
    cannot be honoured the caller has to hear about it."""
    monkeypatch.setenv("LEDGER_VOICEFORGE_URL", "http://127.0.0.1:1")
    with pytest.raises(RuntimeError):
        voice.resolve_tts("voiceforge")
