"""Tokens live in the macOS Keychain, not in the database.

The database holds which accounts are connected and when they were last used.
The tokens themselves — the things that would let someone read your mail — go to
the Keychain, which is encrypted at rest, locked with the login keychain, and
scoped to this app once it is signed.

`security(1)` is used rather than a binding library on purpose: it ships with
macOS, so the .app carries no extra native dependency that would need signing,
entitling and keeping current.

A read failure never crashes Jerry. If the Keychain is unavailable the account
simply presents as needing reconnection, which is honest and recoverable — a
traceback in a menu-bar app is neither.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

SERVICE = "com.jerry.inbox"

# Only macOS has a Keychain. Elsewhere Jerry falls back to the database, which
# is documented rather than silent — see store()/load().
SUPPORTED = sys.platform == "darwin"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["security", *args],
        capture_output=True, text=True, timeout=10,
    )


def available() -> bool:
    if not SUPPORTED:
        return False
    try:
        return _run(["list-keychains"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def store(account: str, secret: str) -> bool:
    """Write a secret, replacing any existing one. True if it landed.

    Deliberately delete-then-add rather than `add -U`.

    `-U` on an item created with a restricted access list makes the Keychain
    ask the user to authorise the change, via a GUI prompt. In a menu-bar app
    with no window that prompt is invisible, so the call simply blocks until it
    times out and the caller quietly falls back to storing the token in the
    database — the exact opposite of the point of this module. Deleting first
    sidesteps authorisation entirely.

    No `-T` either: an empty trusted-application list is what triggers the
    prompt in the first place.
    """
    if not SUPPORTED or not secret:
        return False
    try:
        # Ignore the result: a missing item is the normal case on first write.
        _run(["delete-generic-password", "-a", account, "-s", SERVICE])
        # -w puts the value on argv, briefly visible to `ps`. The alternative,
        # a temp file, leaves it on disk — worse for a longer window.
        result = _run([
            "add-generic-password",
            "-a", account, "-s", SERVICE, "-w", secret,
            "-j", "Jerry mail account token",
        ])
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def load(account: str) -> Optional[str]:
    """Read a secret back. None if it is missing or the Keychain is locked."""
    if not SUPPORTED:
        return None
    try:
        result = _run(["find-generic-password", "-a", account, "-s", SERVICE, "-w"])
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def forget(account: str) -> bool:
    """Remove a secret. Called when an account is disconnected."""
    if not SUPPORTED:
        return False
    try:
        return _run(["delete-generic-password", "-a", account, "-s", SERVICE]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_from_database() -> int:
    """Move any tokens still sitting in the database into the Keychain.

    Run at startup. Idempotent, and it clears the database column only after the
    Keychain write is confirmed — losing a refresh token would mean the user has
    to reconnect, so the order matters.
    """
    if not available():
        return 0

    from .db import Connection, get_session

    moved = 0
    session = get_session()
    try:
        for row in session.query(Connection).all():
            if not row.refresh_token:
                continue
            if store(_key(row), row.refresh_token):
                row.refresh_token = None
                row.access_token = None
                session.add(row)
                moved += 1
        if moved:
            session.commit()
        return moved
    finally:
        session.close()


def _key(connection) -> str:
    return f"{connection.provider}:{connection.account}"
