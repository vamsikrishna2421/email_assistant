"""Connecting a Gmail account, without the user meeting Google Cloud.

The flow is the standard OAuth for *installed* apps: PKCE plus a loopback
redirect. Jerry opens the browser, Google asks the user to approve, the browser
comes back to a one-shot local server, and the code is exchanged for tokens.
The user sees Google's consent screen and nothing else — no console, no project,
no client secret, no redirect URI to paste.

Two things worth knowing before shipping this to other people:

* Gmail read scopes are RESTRICTED. A published app using them needs Google's
  verification and, for restricted scopes, an annual third-party security
  assessment. Until that is done the app can only be used by accounts listed as
  test users, and their tokens expire every 7 days. This is a product blocker,
  not a coding one — see docs/SHIPPING.md.
* `gmail.readonly` is deliberate. Jerry summarises; it does not need to send,
  modify or delete, and asking for less makes both the consent screen and the
  verification review easier. Digest delivery uses `gmail.send`, which is a
  separate, narrower scope than full modify access.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Optional

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1"

# Read to summarise, send to deliver the digest to yourself. Nothing else.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Shipped with the app. For installed apps this is not a secret — Google's own
# docs say so — which is exactly why PKCE exists: the code cannot be exchanged
# by anyone who did not start the flow.
from ..config import load_secrets

load_secrets()
CLIENT_ID = os.environ.get("JERRY_GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("JERRY_GOOGLE_CLIENT_SECRET", "")


class GmailError(RuntimeError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: dt.datetime
    email: str = ""


# ---------------------------------------------------------------------------
# The consent round-trip
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single redirect Google sends the browser back to."""

    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = (query.get("code") or [None])[0]
        _CallbackHandler.error = (query.get("error") or [None])[0]

        ok = _CallbackHandler.code is not None
        title = "You're connected" if ok else "Something went wrong"
        detail = ("You can close this tab and go back to Jerry."
                  if ok else
                  f"Google said: {_CallbackHandler.error}. Close this tab and try again in Jerry.")
        body = f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
<div style="font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:26rem;
            margin:18vh auto;text-align:center;color:#222">
  <div style="font-size:34px">{'&#10003;' if ok else '&#33;'}</div>
  <h1 style="font-size:20px;margin:.6em 0">{title}</h1>
  <p style="color:#666">{detail}</p>
</div>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args):
        pass  # a browser redirect is not worth a console line


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def connect(open_browser: bool = True, timeout: float = 300.0) -> Tokens:
    """Run the whole consent flow. Blocks until the user finishes or gives up."""
    if not CLIENT_ID:
        raise GmailError(
            "Jerry was built without Google credentials. "
            "Set JERRY_GOOGLE_CLIENT_ID before connecting an account."
        )

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)

    # Port 0 = let the OS pick a free one. Google allows any loopback port for
    # installed apps, so nothing has to be pre-registered.
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    _CallbackHandler.code = _CallbackHandler.error = None

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # offline + consent so we actually receive a refresh token; without it
        # the connection silently dies the first time the access token expires.
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)

    thread.join(timeout)
    server.server_close()

    if _CallbackHandler.error:
        raise GmailError(f"Google refused the connection: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise GmailError("The connection timed out. Nothing was changed.")

    return _exchange(_CallbackHandler.code, verifier, redirect_uri)


def _exchange(code: str, verifier: str, redirect_uri: str) -> Tokens:
    data = {
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET

    reply = httpx.post(TOKEN_URL, data=data, timeout=30).json()
    if "access_token" not in reply:
        raise GmailError(reply.get("error_description") or "Google would not issue a token.")

    tokens = Tokens(
        access_token=reply["access_token"],
        refresh_token=reply.get("refresh_token"),
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=reply.get("expires_in", 3600)),
    )
    tokens.email = _whoami(tokens.access_token)
    return tokens


def refresh(refresh_token: str) -> Tokens:
    data = {
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET

    reply = httpx.post(TOKEN_URL, data=data, timeout=30).json()
    if "access_token" not in reply:
        raise GmailError(reply.get("error_description") or "Could not refresh the connection.")
    return Tokens(
        access_token=reply["access_token"],
        # A refresh response usually omits the refresh token; the old one stands.
        refresh_token=reply.get("refresh_token") or refresh_token,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=reply.get("expires_in", 3600)),
    )


def _whoami(access_token: str) -> str:
    try:
        r = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
        )
        return r.json().get("email", "")
    except (httpx.HTTPError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Reading mail
# ---------------------------------------------------------------------------

@dataclass
class Message:
    id: str
    sender: str
    subject: str
    snippet: str
    labels: list[str]

    @property
    def filed_as(self) -> str:
        """Where Gmail put it. A hint for the model, never a verdict."""
        for label, name in (
            ("SPAM", "Spam"), ("TRASH", "Trash"),
            ("CATEGORY_PROMOTIONS", "Promotions"), ("CATEGORY_SOCIAL", "Social"),
            ("CATEGORY_UPDATES", "Updates"), ("CATEGORY_FORUMS", "Forums"),
        ):
            if label in self.labels:
                return name
        return "Primary"

    @property
    def buried(self) -> str:
        """Somewhere the user would not have looked. Empty if it was in plain sight."""
        return self.filed_as if self.filed_as in ("Spam", "Trash", "Promotions", "Social") else ""


def fetch_recent(access_token: str, hours: int = 6, limit: int = 60,
                 exclude_subject: str = "") -> list[Message]:
    """Everything that arrived in the window, INCLUDING spam and promotions.

    Reading Spam is the point rather than an oversight: a real invoice lands
    there often enough that skipping it defeats the exercise. Jerry decides what
    matters itself instead of inheriting Gmail's filing.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    after = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).timestamp())
    query = f"after:{after}"
    if exclude_subject:
        query += f' -subject:"{exclude_subject}"'

    with httpx.Client(headers=headers, timeout=60) as client:
        listing = client.get(
            f"{API}/users/me/messages",
            params={"q": query, "maxResults": limit, "includeSpamTrash": "true"},
        )
        if listing.status_code == 401:
            raise GmailError("The Gmail connection has expired. Reconnect the account.")
        listing.raise_for_status()
        ids = [m["id"] for m in listing.json().get("messages", [])]

        out: list[Message] = []
        for message_id in ids:
            # `metadata` format: headers and a snippet, no bodies. Far smaller,
            # and enough to triage — Jerry never needs to read the letter itself.
            detail = client.get(
                f"{API}/users/me/messages/{message_id}",
                params={"format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date"]},
            )
            if detail.status_code != 200:
                continue
            payload = detail.json()
            headers_map = {h["name"]: h["value"]
                           for h in payload.get("payload", {}).get("headers", [])}
            sender = headers_map.get("From", "unknown sender")
            sender = sender.split("<")[0].replace('"', "").strip() or sender
            out.append(Message(
                id=message_id,
                sender=sender,
                subject=headers_map.get("Subject", "(no subject)").strip(),
                snippet=" ".join(payload.get("snippet", "").split())[:260],
                labels=payload.get("labelIds", []),
            ))
    return out


def send_html(access_token: str, to: str, subject: str, html: str) -> str:
    """Send the digest to the user's own address. Returns the message id."""
    raw = (
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        f"{html}"
    )
    encoded = base64.urlsafe_b64encode(raw.encode()).decode()
    reply = httpx.post(
        f"{API}/users/me/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": encoded}, timeout=60,
    )
    if reply.status_code >= 400:
        raise GmailError(f"Could not send the digest: {reply.text[:200]}")
    return reply.json().get("id", "")


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def import_from_n8n(sqlite_path: str) -> Optional[dict]:
    """Lift an already-working Gmail connection out of the old n8n database.

    Saves reconnecting during the switchover. Returns the credential's decrypted
    data, or None when there is nothing usable to take.
    """
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        row = con.execute(
            "SELECT data FROM credentials_entity WHERE id = 'jerry-gmail'"
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        # n8n encrypts this blob; only an already-decrypted export is usable.
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None
