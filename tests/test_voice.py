"""Voice front end: what gets spoken, what gets heard, and the rule that
a mis-heard word must never approve anything."""

from types import SimpleNamespace

import pytest

import ledger.cli as cli
from ledger import ui, voice


# -- what is worth saying out loud -------------------------------------------

def test_spoken_summary_keeps_the_verdict_drops_the_noise():
    text = "\n".join([
        ui.headline(True, "episode fix-the-test — goal reached", ""),
        ui.kv("plan", ui.plan_chain(["RunSuite", "AuthorPatch:a", "Commit"])),
        ui.kv("verify", ui.ok("0 incidents")),
        ui.kv("journal", "C:\\Users\\reese\\repo\\.ledger\\episode.jsonl"),
        "  audit: ledger vis C:\\Users\\reese\\repo\\.ledger\\episode.jsonl",
    ])
    said = voice.spoken_summary(text)
    assert "goal reached" in said
    assert ".ledger" not in said and "C:\\" not in said   # no paths aloud
    assert "\x1b[" not in said                            # no escape codes


def test_spoken_summary_skips_code_and_diffs():
    said = voice.spoken_summary("\n".join([
        "Here is the fix.",
        "--- a/calc.py",
        "+++ b/calc.py",
        "@@ -1,2 +1,2 @@",
        "-    return a - b",
        "def add(a, b):",
        "$ git commit -m x",
    ]))
    assert said.startswith("Here is the fix")
    assert "return a - b" not in said
    assert "git commit" not in said


def test_spoken_summary_speaks_words_not_glyphs():
    """Status marks and arrows render for the eye; read aloud they are
    punctuation noise. Both the unicode and ASCII fallbacks must go."""
    said = voice.spoken_summary(
        "+ episode one - goal reached\n"
        "plan RunSuite -> AuthorPatch:a -> Commit\n"
        "✓ verify 0 incidents")
    assert not said.startswith("+")
    assert "->" not in said and "→" not in said and "✓" not in said
    assert "then" in said
    assert "goal reached" in said and "0 incidents" in said


def test_spoken_summary_is_bounded():
    said = voice.spoken_summary("word " * 500)
    assert len(said) <= 260


def test_spoken_summary_of_nothing_is_nothing():
    assert voice.spoken_summary("") == ""
    assert voice.spoken_summary("   \n  \n") == ""


# -- transcript cleaning -----------------------------------------------------

def test_whisper_output_is_cleaned(monkeypatch, tmp_path):
    stt = voice.WhisperCppSTT.__new__(voice.WhisperCppSTT)
    stt.binary, stt.model, stt.threads = "whisper", "model", 4

    def fake_run(cmd, **kw):
        return SimpleNamespace(
            returncode=0,
            stdout="[00:00:00.000 --> 00:00:02.000]   fix the failing test\n"
                   "[BLANK_AUDIO]\n"
                   "   in calc dot py\n",
            stderr="")

    monkeypatch.setattr(voice.subprocess, "run", fake_run)
    assert stt.transcribe(tmp_path / "x.wav") == \
        "fix the failing test in calc dot py"


# -- the session front end ---------------------------------------------------

class _Ear:
    """A microphone that always hears the same thing."""

    def __init__(self, heard="fix the failing test"):
        self.heard = heard
        self.listens = 0

    def transcribe(self, wav):
        self.listens += 1
        return self.heard


class _Mouth:
    id = "test"

    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)


class _Recorder:
    def record(self, out_wav, stop_signal):
        stop_signal()
        return out_wav


def _session(heard="fix the failing test", typed=None, out=None):
    ear, mouth = _Ear(heard), _Mouth()
    editor = SimpleNamespace(read=lambda prompt="": (typed or ""))
    vs = voice.VoiceSession(ear, mouth, _Recorder(), editor=editor,
                            output=(out.append if out is not None else
                                    (lambda s: None)))
    return vs, ear, mouth


def test_confirmation_prompts_never_use_the_microphone(monkeypatch):
    """The safety rule: a gate question is answered by the keyboard, so a
    mis-transcribed 'yes' can never approve a push or a PR."""
    out = []
    vs, ear, _ = _session(heard="yes yes absolutely", out=out)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    answer = vs.read("run episode for: 'delete everything'? [Y/n] ")

    assert answer == "n"
    assert ear.listens == 0, "the mic must not be consulted for consent"
    assert any("keyboard" in ui.plain(o) for o in out)


def test_typing_still_works_and_skips_the_mic():
    vs, ear, _ = _session(typed="what does planner.py do?")
    assert vs.read() == "what does planner.py do?"
    assert ear.listens == 0


def test_empty_input_listens():
    vs, ear, _ = _session(heard="fix the failing test", typed="")
    assert vs.read() == "fix the failing test"
    assert ear.listens == 1


def test_replies_are_summarised_not_read_verbatim():
    out = []
    vs, _, mouth = _session(out=out)
    vs.write("Episode succeeded.\n  journal C:\\repo\\.ledger\\ep.jsonl")
    assert out and "journal" in out[0]              # terminal keeps detail
    assert len(mouth.said) == 1
    assert "Episode succeeded" in mouth.said[0]     # speech does not
    assert "journal" not in mouth.said[0]


def test_speaking_can_be_turned_off():
    vs, _, mouth = _session()
    vs.speak_replies = False
    vs.write("Episode succeeded.")
    assert mouth.said == []


def test_a_broken_microphone_does_not_kill_the_session():
    class Exploding:
        def record(self, out_wav, stop_signal):
            raise RuntimeError("no input device")

    vs, _, _ = _session(typed="")
    vs.recorder = Exploding()
    assert vs.listen() == ""        # reported, not raised


def test_a_dead_microphone_stands_down_instead_of_looping():
    """Regression: read() used to retry forever when nothing was heard, so
    a broken mic trapped the session with no exit but Ctrl+C."""
    out = []
    vs, ear, _ = _session(heard="", typed="", out=out)   # hears nothing, ever

    assert vs.read() == ""                                # gives up
    assert ear.listens == voice.MAX_EMPTY_LISTENS
    assert vs.listening is False
    assert any("switching to typing" in ui.plain(o) for o in out)

    ear.listens = 0
    assert vs.read() == ""          # stays in typing mode, no more recording
    assert ear.listens == 0


# -- CLI wiring --------------------------------------------------------------

def test_voice_refuses_to_skip_permissions(capsys):
    args = SimpleNamespace(dangerously_skip_permissions=True)
    assert cli._voice_session(args, {}, None) is None
    assert "never" in capsys.readouterr().err


def test_voice_diagnostics_shape():
    rows = voice.diagnostics()
    labels = [label for label, _, _ in rows]
    assert "voice: recorder" in labels
    assert "voice: speech-to-text" in labels
    assert "voice: text-to-speech" in labels
    assert all(isinstance(ok, bool) for _, _, ok in rows)


def test_null_tts_is_silent_not_broken():
    voice.NullTTS().speak("anything")            # must not raise
    assert voice.resolve_tts("off").id == "off"
