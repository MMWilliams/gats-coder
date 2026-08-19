"""Voice for the unified session: speak to it, hear the outcome.

No new Python dependencies -- the same "shell out to a real tool" pattern
the LLM backends use. ffmpeg records, whisper.cpp transcribes locally,
piper speaks. Audio never leaves the machine unless a cloud backend is
chosen on purpose, which matters for a tool whose pitch is local-first.

Two rules the design is built around:

1. **Voice never approves a gate.** "yes" is the most mis-transcribed word
   there is, and here it can push a branch or open a pull request. When the
   session asks for confirmation, this module hands the keyboard back.
2. **Never read code aloud.** The terminal keeps the full detail; the
   spoken channel gets a summary, because a diff read out is noise.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import ui

HOME_DIR = Path.home() / ".ledger"


# -- locating the tools ------------------------------------------------------

def _first_existing(*paths):
    for p in paths:
        if p and Path(p).is_file():
            return str(p)
    return None


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def find_player() -> list[str] | None:
    if shutil.which("ffplay"):
        return [shutil.which("ffplay"), "-nodisp", "-autoexit",
                "-loglevel", "quiet"]
    if sys.platform == "darwin" and shutil.which("afplay"):
        return [shutil.which("afplay")]
    if shutil.which("aplay"):
        return [shutil.which("aplay"), "-q"]
    return None


def find_whisper() -> tuple[str | None, str | None]:
    """(binary, model). Env overrides win, then ~/.ledger/whisper."""
    exe = os.environ.get("LEDGER_WHISPER_BIN") or shutil.which("whisper-cli")
    if not exe:
        exe = _first_existing(
            HOME_DIR / "whisper" / "Release" / "whisper-cli.exe",
            HOME_DIR / "whisper" / "whisper-cli.exe",
            HOME_DIR / "whisper" / "whisper-cli",
            HOME_DIR / "whisper" / "build" / "bin" / "whisper-cli")
    model = os.environ.get("LEDGER_WHISPER_MODEL")
    if not model:
        models = HOME_DIR / "whisper" / "models"
        found = sorted(models.glob("ggml-*.bin")) if models.is_dir() else []
        model = str(found[0]) if found else None
    return exe, model


def find_piper() -> tuple[str | None, str | None]:
    """(binary, voice model)."""
    exe = os.environ.get("LEDGER_PIPER_BIN") or shutil.which("piper")
    model = os.environ.get("LEDGER_PIPER_VOICE")
    if not model:
        voices = HOME_DIR / "voices"
        found = sorted(voices.glob("*.onnx")) if voices.is_dir() else []
        model = str(found[0]) if found else None
    return exe, model


# -- microphone --------------------------------------------------------------

def list_input_devices() -> list[str]:
    """Names ffmpeg will accept for -i, best effort per platform."""
    ff = find_ffmpeg()
    if not ff or sys.platform != "win32":
        return []
    proc = subprocess.run([ff, "-hide_banner", "-list_devices", "true",
                           "-f", "dshow", "-i", "dummy"],
                          capture_output=True, text=True, errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    return re.findall(r'"([^"]+)"\s*\(audio\)', out)


def _mic_args(device: str | None) -> list[str]:
    system = platform.system()
    if system == "Windows":
        dev = device or (list_input_devices() or [None])[0]
        if not dev:
            raise RuntimeError("no microphone found (ffmpeg -list_devices)")
        return ["-f", "dshow", "-i", f"audio={dev}"]
    if system == "Darwin":
        return ["-f", "avfoundation", "-i", f":{device or '0'}"]
    return ["-f", "pulse", "-i", device or "default"]


class Recorder:
    """Push-to-talk capture straight to the 16 kHz mono WAV whisper wants."""

    def __init__(self, device: str | None = None, max_seconds: int = 120):
        self.ffmpeg = find_ffmpeg()
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is required to record audio")
        self.device = device
        self.max_seconds = max_seconds

    def record(self, out_wav: Path, stop_signal) -> Path:
        """Record until `stop_signal()` returns (it blocks, e.g. waiting on
        Enter), then close the file cleanly."""
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               *_mic_args(self.device),
               "-t", str(self.max_seconds),
               "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        try:
            stop_signal()
        finally:
            try:
                # 'q' is ffmpeg's clean stop: it finalises the WAV header.
                proc.communicate(input="q", timeout=10)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
        return out_wav


# -- speech to text ----------------------------------------------------------

_TIMESTAMP = re.compile(r"^\s*\[[\d:.\s\->]+\]\s*")
_NON_SPEECH = re.compile(
    r"[\(\[](BLANK_AUDIO|blank_audio|music|silence|inaudible|sound)"
    r"[^\)\]]*[\)\]]", re.IGNORECASE)


class WhisperCppSTT:
    """Local transcription via the whisper.cpp CLI. Nothing leaves the box."""

    id = "whisper.cpp"

    def __init__(self, binary: str | None = None, model: str | None = None,
                 threads: int | None = None):
        found_bin, found_model = find_whisper()
        self.binary = binary or found_bin
        self.model = model or found_model
        if not self.binary or not self.model:
            raise RuntimeError(
                "whisper.cpp not found: set LEDGER_WHISPER_BIN and "
                "LEDGER_WHISPER_MODEL, or install into ~/.ledger/whisper")
        self.threads = threads or max(2, (os.cpu_count() or 4) // 2)

    def transcribe(self, wav: Path) -> str:
        proc = subprocess.run(
            [self.binary, "-m", self.model, "-f", str(wav), "-nt",
             "-t", str(self.threads)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"whisper failed: {proc.stderr.strip()[:300]}")
        lines = [_TIMESTAMP.sub("", ln).strip()
                 for ln in proc.stdout.splitlines()]
        text = " ".join(ln for ln in lines if ln)
        text = _NON_SPEECH.sub("", text)      # tags are not words
        return re.sub(r"\s+", " ", text).strip()


# -- text to speech ----------------------------------------------------------

class PiperTTS:
    """Local neural speech. Needs a .onnx voice in ~/.ledger/voices."""

    id = "piper"

    def __init__(self, binary: str | None = None, model: str | None = None):
        found_bin, found_model = find_piper()
        self.binary = binary or found_bin
        self.model = model or found_model
        self.player = find_player()
        if not self.binary or not self.model:
            raise RuntimeError(
                "piper not found: install piper-tts and put a voice in "
                "~/.ledger/voices (or set LEDGER_PIPER_VOICE)")
        if not self.player:
            raise RuntimeError("no audio player found (install ffmpeg)")

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        tmp = Path(tempfile.gettempdir()) / f"ledger-say-{os.getpid()}.wav"
        try:
            subprocess.run([self.binary, "-m", self.model, "-f", str(tmp)],
                           input=text, capture_output=True, text=True,
                           timeout=120)
            if tmp.is_file() and tmp.stat().st_size > 44:
                subprocess.run([*self.player, str(tmp)],
                               capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            pass          # a mute assistant beats a crashed one
        finally:
            tmp.unlink(missing_ok=True)


class SapiTTS:
    """Zero-install Windows fallback: the built-in speech synthesiser."""

    id = "sapi"

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("SAPI is Windows-only")
        self.powershell = shutil.which("powershell") or "powershell"

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        safe = text.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech; "
                  "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Speak('" + safe + "')")
        try:
            subprocess.run([self.powershell, "-NoProfile", "-Command", script],
                           capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            pass


class NullTTS:
    id = "off"

    def speak(self, text: str) -> None:
        return


def resolve_tts(kind: str = "auto"):
    """piper -> sapi -> silent, unless a specific one is demanded."""
    kind = (kind or "auto").lower()
    if kind in ("off", "none"):
        return NullTTS()
    if kind == "piper":
        return PiperTTS()
    if kind == "sapi":
        return SapiTTS()
    for factory in (PiperTTS, SapiTTS):
        try:
            return factory()
        except Exception:
            continue
    return NullTTS()


# -- what is worth saying out loud -------------------------------------------

_SKIP_LINE = re.compile(
    r"^\s*(?:[\\/]|[A-Za-z]:[\\/]|\$|>|#|\||[-+]{3}|@@|\d+\s*\|)")
_PATHY = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s]{8,}")
# Code read aloud is noise: keywords, calls, and operator soup all disqualify
# a line from the spoken channel while the terminal still shows it in full.
_CODEY = re.compile(
    r"[{}<>;=]{2,}|::|-->|\w+\([^)]*\)"
    r"|\b(?:def|class|import|from|return|elif|lambda|assert)\b")


def spoken_summary(text: str, limit: int = 260) -> str:
    """Condense terminal output into something worth hearing.

    Paths, diffs, command lines and decision tables are for the eyes. This
    keeps the prose and the verdicts.
    """
    plain = ui.plain(text or "")
    kept: list[str] = []
    for raw in plain.splitlines():
        line = raw.strip()
        if not line or _SKIP_LINE.match(line) or _CODEY.search(line):
            continue
        had_path = bool(_PATHY.search(line))
        line = _PATHY.sub("", line)
        # Status glyphs and arrows are shorthand for the eye. Speech needs
        # words -- and both the unicode and the ASCII fallbacks appear,
        # depending on the console the output was rendered for.
        line = re.sub(r"^\s*[+x*]\s+", "", line)
        line = re.sub(r"->|→", " then ", line)
        line = re.sub(r"[·•▌◀✓✗●]", " ", line)
        line = re.sub(r"\s+", " ", line).strip(" -")
        # A line that was mostly a path ("journal C:\...") is a path line;
        # its leftover label is not worth hearing.
        if had_path and len(line.split()) <= 2:
            continue
        if len(line) < 3:
            continue
        kept.append(line)
        if sum(len(k) for k in kept) > limit:
            break
    out = ". ".join(kept)
    out = re.sub(r"\.\s*\.", ".", out)
    return out[:limit].strip()


# -- the session front end ---------------------------------------------------

LISTEN_HINT = "press Enter to talk (or just type)"

# Consecutive silent/failed listens before voice input stands down. Without
# a ceiling a dead microphone traps the session in a retry loop with no way
# out but Ctrl+C -- the opposite of hands-free.
MAX_EMPTY_LISTENS = 3


class VoiceSession:
    """Wraps the session's input and output so speech is additive: you can
    always type instead, and confirmations always come from the keyboard."""

    def __init__(self, stt, tts, recorder, editor=None, output=print,
                 speak_replies: bool = True):
        self.stt = stt
        self.tts = tts
        self.recorder = recorder
        self.editor = editor
        self.output = output
        self.speak_replies = speak_replies
        self.listening = True      # cleared if the microphone keeps failing

    # -- input ---------------------------------------------------------------
    def listen(self) -> str:
        wav = Path(tempfile.gettempdir()) / f"ledger-ear-{os.getpid()}.wav"
        self.output(ui.accent("  * recording - press Enter to stop"))

        def wait_for_enter():
            # OSError too: stdin may be closed or redirected, and a
            # recorder that raises there would look like a broken mic.
            try:
                input()
            except (EOFError, KeyboardInterrupt, OSError):
                pass

        try:
            self.recorder.record(wav, wait_for_enter)
            self.output(ui.dim("  transcribing..."))
            text = self.stt.transcribe(wav)
        except Exception as exc:
            self.output(ui.bad(f"  voice input failed: {exc}"))
            return ""
        finally:
            wav.unlink(missing_ok=True)
        if text:
            self.output(ui.accent("> ") + text)
        else:
            self.output(ui.dim("  heard nothing"))
        return text

    def read(self, prompt: str = "") -> str:
        # A prompt means the session is ASKING something -- a gate or a
        # confirmation. Those are keyboard-only, on purpose.
        if prompt and prompt.strip() and prompt.strip() not in (">", "❯"):
            self.output(ui.warn(prompt.strip()) + ui.dim("  [keyboard]"))
            try:
                return input("> ")
            except (EOFError, KeyboardInterrupt):
                return "n"
        empties = 0
        while True:
            typed = (self.editor.read(prompt) if self.editor
                     else input("> " + LISTEN_HINT + ": "))
            if typed.strip() or not self.listening:
                return typed
            spoken = self.listen()
            if spoken.strip():
                return spoken
            empties += 1
            if empties >= MAX_EMPTY_LISTENS:
                self.listening = False
                self.output(ui.warn(
                    "  voice input is not producing anything — switching to "
                    "typing. Check `ledger doctor`, then restart to retry."))
                return ""

    # -- output --------------------------------------------------------------
    def write(self, text: str) -> None:
        self.output(text)
        if not self.speak_replies:
            return
        summary = spoken_summary(text)
        if summary:
            try:
                self.tts.speak(summary)
            except KeyboardInterrupt:
                pass


def diagnostics() -> list[tuple[str, str, bool]]:
    """(label, detail, ok) rows for `ledger doctor`."""
    rows = []
    ff = find_ffmpeg()
    rows.append(("voice: recorder",
                 ff or "ffmpeg NOT FOUND (needed to record)", bool(ff)))
    exe, model = find_whisper()
    rows.append(("voice: speech-to-text",
                 f"whisper.cpp + {Path(model).name}" if exe and model
                 else "not installed (see: ledger voice --setup)",
                 bool(exe and model)))
    pexe, pmodel = find_piper()
    if pexe and pmodel:
        detail, ok = f"piper {Path(pmodel).stem}", True
    elif sys.platform == "win32":
        detail, ok = "piper voice missing; falling back to Windows SAPI", True
    else:
        detail, ok = "no text-to-speech found", False
    rows.append(("voice: text-to-speech", detail, ok))
    if sys.platform == "win32":
        devices = list_input_devices()
        rows.append(("voice: microphone",
                     devices[0] if devices else "no input device found",
                     bool(devices)))
    return rows
