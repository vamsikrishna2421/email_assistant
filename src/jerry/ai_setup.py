"""Fully-managed, zero-touch local-AI setup for macOS.

The user picks a speed tier and never learns what "Ollama" is. This module:

  1. finds or silently installs the Ollama runtime,
  2. starts its local server,
  3. downloads the chosen model with real progress,
  4. reports readiness.

Adapted from VibeFlow's ai_setup.py, which does the same job on Windows via
winget. macOS differs in three ways that matter:

  * No winget. Homebrew if present, otherwise the official signed .dmg, which
    needs no admin rights because it installs into ~/Applications.
  * Ollama binds 127.0.0.1 only. Talking to it as "localhost" resolves to ::1
    first and fails with a bare connection error, so every URL here is the IPv4
    literal. That one cost an afternoon.
  * Intel Macs have no GPU path at all — Ollama's Metal backend is Apple Silicon
    only — so inference is CPU-bound and the honest time estimates below are
    minutes, not seconds.

Standard library plus the Ollama HTTP API. Every step is defensive and reports
progress through a callback so the UI can say what is happening in plain words.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

ENDPOINT = "http://127.0.0.1:11434"
OLLAMA_DMG_URL = "https://ollama.com/download/Ollama.dmg"

Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

def ollama_exe() -> Optional[str]:
    """Path to the ollama binary, including the places a GUI app will not see.

    A .app launched from Finder does not inherit the shell PATH, so a Homebrew
    install at /opt/homebrew/bin is invisible unless we look for it directly.
    """
    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
        "/Applications/Ollama.app/Contents/Resources/ollama",
        os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
    ]
    return next((p for p in candidates if os.path.exists(p)), None)


def server_up(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{ENDPOINT}/api/version", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def is_apple_silicon() -> bool:
    return platform.machine() in ("arm64", "aarch64")


def speed_note() -> str:
    """An honest sentence about what to expect, shown before the first run."""
    if is_apple_silicon():
        return "Your Mac can run this quickly — most summaries finish in under a minute."
    return (
        "Your Mac does the thinking on its processor rather than a graphics chip, "
        "so a summary takes a few minutes. It runs in the background on a schedule, "
        "so you should never be waiting on it."
    )


# ---------------------------------------------------------------------------
# Installing the runtime
# ---------------------------------------------------------------------------

def install_runtime(progress: Progress = _noop) -> bool:
    """Install Ollama without the user seeing a terminal. True if usable after."""
    if ollama_exe():
        return True

    if shutil.which("brew"):
        progress("Setting up the local AI engine…")
        try:
            subprocess.run(
                ["brew", "install", "ollama"],
                check=True, capture_output=True, timeout=900,
            )
            if ollama_exe():
                return True
        except (subprocess.SubprocessError, OSError):
            pass  # fall through to the .dmg

    progress("Downloading the local AI engine…")
    try:
        dmg = os.path.join("/tmp", "Ollama.dmg")
        urllib.request.urlretrieve(OLLAMA_DMG_URL, dmg)

        progress("Installing the local AI engine…")
        mount = subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-quiet", dmg],
            capture_output=True, text=True, timeout=300,
        )
        if mount.returncode != 0:
            return False
        volume = "/Volumes/Ollama"
        try:
            target = os.path.expanduser("~/Applications")
            os.makedirs(target, exist_ok=True)
            src = os.path.join(volume, "Ollama.app")
            if os.path.exists(src):
                subprocess.run(["cp", "-R", src, target], check=True, timeout=300)
        finally:
            subprocess.run(["hdiutil", "detach", volume, "-quiet"],
                           capture_output=True, timeout=120)
        try:
            os.remove(dmg)
        except OSError:
            pass
    except (subprocess.SubprocessError, OSError, urllib.error.URLError):
        return False

    return ollama_exe() is not None


def start_server(progress: Progress = _noop) -> bool:
    """Start the local server if it is not already answering."""
    if server_up():
        return True
    exe = ollama_exe()
    if not exe:
        return False

    progress("Starting the local AI engine…")
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,   # survives this process exiting
        )
    except OSError:
        return False

    for _ in range(40):          # up to ~20s; a cold start is not instant
        if server_up():
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def installed_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{ENDPOINT}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []


def have_model(model: str) -> bool:
    names = installed_models()
    # Ollama reports "qwen2.5:7b"; a bare "qwen2.5" should still match.
    return any(n == model or n.split(":")[0] == model.split(":")[0] for n in names)


def pull_model(model: str, progress: Progress = _noop) -> bool:
    """Download a model, reporting percentage as it goes.

    Ollama streams newline-delimited JSON with completed/total byte counts. We
    translate that into one plain sentence rather than surfacing layer digests.
    """
    if have_model(model):
        return True

    payload = json.dumps({"model": model, "stream": True}).encode()
    request = urllib.request.Request(
        f"{ENDPOINT}/api/pull", data=payload,
        headers={"Content-Type": "application/json"},
    )

    last_pct = -5
    try:
        with urllib.request.urlopen(request, timeout=7200) as response:
            for line in response:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("error"):
                    progress(f"Download failed: {event['error']}")
                    return False
                total, done = event.get("total"), event.get("completed")
                if total and done:
                    pct = int(done * 100 / total)
                    if pct >= last_pct + 5:      # avoid a flood of near-identical updates
                        last_pct = pct
                        progress(f"Downloading the AI model… {pct}%")
                elif event.get("status") == "success":
                    progress("AI model ready.")
    except (urllib.error.URLError, OSError):
        return False

    return have_model(model)


# ---------------------------------------------------------------------------
# The one call the UI makes
# ---------------------------------------------------------------------------

def ensure_ready(model: str, progress: Progress = _noop) -> tuple[bool, str]:
    """Get from nothing to a working local model. Returns (ok, plain message)."""
    if not install_runtime(progress):
        return False, (
            "Could not set up the local AI engine automatically. "
            "You can install it yourself from ollama.com and then reopen Jerry."
        )
    if not start_server(progress):
        return False, "The local AI engine would not start. Restarting your Mac usually clears this."
    if not pull_model(model, progress):
        return False, "The AI model could not be downloaded. Check your internet connection and try again."
    return True, "Ready."


def steps_for(model: str) -> Iterator[str]:
    """What still needs doing, in plain words — for a setup screen checklist."""
    if not ollama_exe():
        yield "Install the local AI engine"
    if not server_up():
        yield "Start the local AI engine"
    if not have_model(model):
        yield "Download the AI model"
