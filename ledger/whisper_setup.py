"""Fetch whisper.cpp so speech-to-text is one answer rather than an errand.

This was the last manual step in setup: "download a binary from a GitHub
releases page and a model from HuggingFace, and put them here". Everything
else could be installed by answering a question.

Downloading an executable is a supply-chain decision, so the rules are:

* The URL is printed before anything is fetched, and nothing is fetched
  without being asked.
* A package manager is preferred where it has the package, because then the
  provenance is the manager's problem and not ours -- Homebrew ships
  whisper-cpp, so macOS never downloads a loose binary here.
* What arrives is checked before it is kept. A GGML model has a magic
  number; a truncated or redirected download does not.
"""

from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path

HOME_DIR = Path.home() / ".ledger"
WHISPER_DIR = HOME_DIR / "whisper"
MODELS_DIR = WHISPER_DIR / "models"

# ggml-base.en: ~148 MB, English-only, and comfortably fast enough on CPU
# for push-to-talk. The larger models buy accuracy this use does not need.
MODEL_NAME = "ggml-base.en.bin"
MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
             f"{MODEL_NAME}")

# Resolved from the releases API rather than hardcoded. Upstream tags move
# (they are build numbers like b4938, not semver), so a pinned URL is a 404
# waiting to happen -- the first version of this shipped one.
RELEASES_API = ("https://api.github.com/repos/ggml-org/whisper.cpp/"
                "releases/latest")

#: platform -> (asset-name substring, archive kind)
ASSETS = {
    "windows": ("whisper-bin-x64.zip", "zip"),
    "linux": ("whisper-bin-ubuntu-x64.tar.gz", "tar"),
}

# GGML files begin with this. Checked so a captive-portal HTML page or a
# truncated transfer is rejected rather than stored and failed on later,
# at the first thing someone says into a microphone.
GGML_MAGIC = b"ggml"


def model_path() -> Path:
    return MODELS_DIR / MODEL_NAME


def have_model() -> bool:
    p = model_path()
    return p.is_file() and p.stat().st_size > 1_000_000


def have_binary() -> bool:
    from .voice import find_whisper
    exe, _ = find_whisper()
    return bool(exe)


def _download(url: str, dest: Path, echo=print) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    echo(f"      {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            step = max(total // 20, 1 << 20)
            nudge = step
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if done >= nudge:
                        nudge += step
                        if total:
                            echo(f"      {done * 100 // total}%  "
                                 f"({done >> 20} of {total >> 20} MB)")
                        else:
                            echo(f"      {done >> 20} MB")
    except Exception as exc:                     # network, TLS, 404, disk
        echo(f"      download failed: {exc}")
        tmp.unlink(missing_ok=True)
        return False
    # Move into place only once the whole file is here: a half-written model
    # at the real path looks installed and fails at the first utterance.
    tmp.replace(dest)
    return True


def fetch_model(echo=print) -> bool:
    if have_model():
        echo(f"      model already at {model_path()}")
        return True
    if not _download(MODEL_URL, model_path(), echo=echo):
        return False
    with open(model_path(), "rb") as fh:
        head = fh.read(4)
    if head != GGML_MAGIC:
        echo("      what downloaded is not a GGML model; discarding it")
        model_path().unlink(missing_ok=True)
        return False
    echo(f"      model ready: {model_path()}")
    return True


def latest_asset(want: str, echo=print) -> str | None:
    """The download URL of the newest release asset whose name matches."""
    import json
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"user-agent": "ledger-setup"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        echo(f"      could not reach the releases API: {exc}")
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == want:
            return asset.get("browser_download_url")
    echo(f"      no asset named {want} in release "
         f"{release.get('tag_name', '?')}")
    return None


def _safe_members(names) -> bool:
    """Reject absolute paths and traversal. Extraction honours whatever
    paths an archive contains, and an archive is an arbitrary list."""
    for name in names:
        if name.startswith(("/", "\\")) or ".." in Path(name).parts \
                or (len(name) > 1 and name[1] == ":"):
            return False
    return True


def fetch_binary(echo=print) -> bool:
    """The whisper-cli executable, by whatever route the platform offers."""
    import tarfile

    from . import setup as deps

    if have_binary():
        echo("      whisper-cli already present")
        return True

    system = deps.system()
    if system == "macos":
        if shutil.which("brew"):
            # Provenance is Homebrew's problem rather than a loose zip's.
            return deps.run_install(["brew", "install", "whisper-cpp"],
                                    echo=echo)
        echo("      install Homebrew, then: brew install whisper-cpp")
        return False

    spec = ASSETS.get(system)
    if spec is None:
        return False
    name, kind = spec
    url = latest_asset(name, echo=echo)
    if url is None:
        return False

    archive = WHISPER_DIR / name
    if not _download(url, archive, echo=echo):
        return False
    try:
        if kind == "zip":
            with zipfile.ZipFile(archive) as zf:
                if not _safe_members(zf.namelist()):
                    echo("      refusing an archive with paths outside it")
                    return False
                zf.extractall(WHISPER_DIR)
        else:
            with tarfile.open(archive) as tf:
                if not _safe_members(tf.getnames()):
                    echo("      refusing an archive with paths outside it")
                    return False
                tf.extractall(WHISPER_DIR)
            for found in WHISPER_DIR.rglob("whisper-cli"):
                found.chmod(0o755)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        echo(f"      could not unpack: {exc}")
        return False
    finally:
        archive.unlink(missing_ok=True)

    if have_binary():
        echo(f"      whisper-cli ready under {WHISPER_DIR}")
        return True
    echo(f"      unpacked to {WHISPER_DIR} but no whisper-cli was found in it")
    return False


def install(echo=print) -> bool:
    """Binary and model. True only when speech will actually work."""
    ok_bin = fetch_binary(echo=echo)
    ok_model = fetch_model(echo=echo)
    return ok_bin and ok_model
