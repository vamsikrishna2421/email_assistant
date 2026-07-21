"""Where Jerry keeps its things, and what it remembers between runs.

Everything lives under ~/Library/Application Support/Jerry rather than beside
the source. That is the macOS convention, it survives moving or reinstalling the
app, and — learned the hard way — it keeps the data out of ~/Documents, where
macOS lets a background agent see files but not read them. A scheduled job in a
protected folder fails with a bare "Operation not permitted" and no hint that
permissions are the cause.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "Jerry"


def _support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()


DATA_DIR = Path(os.environ.get("JERRY_DATA_DIR") or _support_dir())
DB_PATH = DATA_DIR / "jerry.sqlite3"
LOG_DIR = DATA_DIR / "logs"
SETTINGS_PATH = DATA_DIR / "settings.json"

# The dashboard binds to loopback only. Nothing Jerry runs should be reachable
# from the network — it holds a live Gmail token.
HOST = "127.0.0.1"
PORT = int(os.environ.get("JERRY_PORT", "8765"))


def load_secrets() -> None:
    """Read google.env into the environment.

    A .app launched from Finder inherits none of the shell environment, so the
    OAuth client has to come from a file next to the database rather than from
    an exported variable. Called before anything touches a connector.
    """
    path = DATA_DIR / "google.env"
    try:
        for line in path.read_text("utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # The database and token store are readable only by this user.
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Model tiers
#
# The user picks a speed/quality tier and never sees a model name. Tiers are
# ordered by measured behaviour on a 2018 Intel MacBook Pro (CPU-only, since
# Ollama has no GPU path on Intel Macs), classifying a real 34-email inbox:
#
#   qwen2.5:7b    ~9 min   held the output format on the real inbox
#   qwen3.5:4b    faster   won every synthetic benchmark, then abandoned the
#                          output format on the real inbox and left 33 of 34
#                          emails unclassified
#
# That is why "balanced" is the default rather than the faster model, and why
# scripts/eval-models.mjs exists: synthetic benchmarks chose wrong here twice.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    key: str
    label: str
    blurb: str
    model: str
    download: str


TIERS: tuple[Tier, ...] = (
    Tier("fast", "Fast",
         "Quickest to finish. Good enough for sorting obvious junk from the rest.",
         "qwen2.5:3b", "2 GB"),
    Tier("balanced", "Balanced",
         "Recommended. Noticeably better judgement, still comfortable on most machines.",
         "qwen2.5:7b", "4.7 GB"),
    Tier("thorough", "Thorough",
         "Best judgement, slowest. Worth it if your inbox is busy and subtle.",
         "qwen3.5:9b", "6.6 GB"),
)

TIERS_BY_KEY = {t.key: t for t in TIERS}
DEFAULT_TIER = "balanced"


@dataclass
class Settings:
    """User-facing settings. Preferences about *email* live in the database."""

    tier: str = DEFAULT_TIER
    digest_hours: int = 6
    deliver_email: bool = True
    run_at_login: bool = True
    onboarded: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def model(self) -> str:
        return TIERS_BY_KEY.get(self.tier, TIERS_BY_KEY[DEFAULT_TIER]).model

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(SETTINGS_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f for f in cls.__dataclass_fields__ if f != "extras"}
        return cls(
            **{k: v for k, v in raw.items() if k in known},
            extras={k: v for k, v in raw.items() if k not in known},
        )

    def save(self) -> None:
        ensure_dirs()
        payload = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "extras"}
        payload.update(self.extras)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        tmp.replace(SETTINGS_PATH)
