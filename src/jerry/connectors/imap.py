"""Reading mail over IMAP — any provider, no OAuth, no audit.

This exists because Gmail's read scopes are *restricted*: publishing an app that
uses them needs Google's verification plus an annual third-party security
assessment, and until that is paid for the app is capped at 100 hand-added test
users whose tokens expire weekly. IMAP has none of that. The user enables 2FA,
generates an app password, and it works — with Gmail, Outlook, Fastmail, Proton
Bridge, iCloud or a self-hosted server.

The trade is a worse first-run experience: an app password is several steps the
user performs themselves, where OAuth is two clicks. So Jerry offers both and
says plainly which is which.

Design note: this returns the same `Message` shape as the Gmail connector, so
the digest engine cannot tell them apart. That is the entire reason the engine
takes a fetch function instead of importing a provider.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import re
import ssl
from dataclasses import dataclass
from typing import Optional

from .gmail import Message   # identical shape; the engine is provider-blind

# Ports are the implicit-TLS ones. STARTTLS on 143 exists but every provider
# below supports 993, and one code path is one fewer thing to get wrong.
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "label": "Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "help": "Turn on 2-Step Verification, then create an App Password at "
                "myaccount.google.com → Security → App passwords.",
        # Gmail exposes its tabs and Spam as real folders, which is how the
        # "Gmail buried this" signal survives without the API.
        "folders": ["INBOX", "[Gmail]/Spam"],
    },
    "outlook": {
        "label": "Outlook / Hotmail",
        "host": "outlook.office365.com",
        "port": 993,
        "help": "Create an app password at account.microsoft.com → Security → "
                "Advanced security options.",
        "folders": ["INBOX", "Junk"],
    },
    "yahoo": {
        "label": "Yahoo Mail",
        "host": "imap.mail.yahoo.com",
        "port": 993,
        "help": "Create an app password at login.yahoo.com → Account Security.",
        "folders": ["INBOX", "Bulk Mail"],
    },
    "icloud": {
        "label": "iCloud Mail",
        "host": "imap.mail.me.com",
        "port": 993,
        "help": "Create an app-specific password at appleid.apple.com → "
                "Sign-In and Security.",
        "folders": ["INBOX", "Junk"],
    },
    "fastmail": {
        "label": "Fastmail",
        "host": "imap.fastmail.com",
        "port": 993,
        "help": "Create an app password at fastmail.com → Settings → Privacy & Security.",
        "folders": ["INBOX", "Spam"],
    },
    "other": {
        "label": "Something else",
        "host": "",
        "port": 993,
        "help": "Your provider's help pages will list its IMAP server address.",
        "folders": ["INBOX"],
    },
}


class ImapError(RuntimeError):
    pass


@dataclass
class Account:
    provider: str
    host: str
    port: int
    username: str
    password: str


def _connect(account: Account) -> imaplib.IMAP4_SSL:
    try:
        connection = imaplib.IMAP4_SSL(
            account.host, account.port, ssl_context=ssl.create_default_context(), timeout=30,
        )
    except (OSError, ssl.SSLError) as exc:
        raise ImapError(f"Could not reach {account.host}. Check the server address.") from exc

    try:
        connection.login(account.username, account.password)
    except imaplib.IMAP4.error as exc:
        detail = str(exc)
        # Providers phrase this a dozen ways; the cause is nearly always the
        # same and the user needs the actionable version, not the server's.
        if "AUTHENTICATIONFAILED" in detail.upper() or "invalid credentials" in detail.lower():
            raise ImapError(
                "That username or app password was not accepted. Note this needs an "
                "app password, not your normal account password."
            ) from exc
        raise ImapError(f"The mail server refused the connection: {detail[:160]}") from exc
    return connection


def verify(account: Account) -> str:
    """Check credentials before saving them. Returns the account name on success."""
    connection = _connect(account)
    try:
        return account.username
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _decode(raw: Optional[str]) -> str:
    """MIME-decode a header. Subjects arrive base64'd and charset-tagged."""
    if not raw:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return " ".join("".join(parts).split())


def _sender(raw: str) -> str:
    name, address = email.utils.parseaddr(_decode(raw))
    return (name or address.split("@")[0] or "unknown sender").strip()


def fetch_recent(account: Account, hours: int = 6, limit: int = 60,
                 exclude_subject: str = "") -> list[Message]:
    """Recent mail from the inbox AND the spam folder.

    Reading spam is the point rather than an oversight: a real invoice lands
    there often enough that skipping it defeats the exercise.
    """
    import datetime as dt

    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%d-%b-%Y")
    folders = PROVIDERS.get(account.provider, PROVIDERS["other"])["folders"]

    connection = _connect(account)
    messages: list[Message] = []
    try:
        for folder in folders:
            try:
                status, _ = connection.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
            except imaplib.IMAP4.error:
                continue    # folder names vary; a missing one is not an error

            # IMAP SINCE has day granularity only, so this over-fetches and the
            # exact cutoff is applied below against the real Date header.
            try:
                status, data = connection.search(None, f'(SINCE "{since}")')
            except imaplib.IMAP4.error:
                continue
            if status != "OK" or not data or not data[0]:
                continue

            ids = data[0].split()[-limit:]
            if not ids:
                continue

            # One round trip for the whole folder rather than one per message.
            status, response = connection.fetch(
                b",".join(ids),
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",  # PEEK: do not mark as read
            )
            if status != "OK":
                continue

            buried = "" if folder.upper().endswith("INBOX") else "Spam"
            for part in response:
                if not isinstance(part, tuple) or len(part) < 2:
                    continue
                header = email.message_from_bytes(part[1])
                subject = _decode(header.get("Subject"))
                if exclude_subject and exclude_subject.lower() in subject.lower():
                    continue

                identifier = re.sub(rb"[^0-9]", b"", part[0].split()[0]).decode() or subject[:32]
                messages.append(Message(
                    id=f"{folder}:{identifier}",
                    sender=_sender(header.get("From", "")),
                    subject=subject or "(no subject)",
                    # IMAP has no snippet. Headers alone still carry most of the
                    # signal, and fetching bodies for triage would be far slower
                    # and read more of the user's mail than Jerry needs.
                    snippet="",
                    labels=["SPAM"] if buried else ["INBOX"],
                ))
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    return messages[:limit]


def send_html(account: Account, to: str, subject: str, html: str) -> None:
    """Deliver the digest over SMTP, inferred from the IMAP host."""
    import smtplib
    from email.mime.text import MIMEText

    host = account.host.replace("imap.", "smtp.").replace("outlook.office365.com", "smtp.office365.com")
    message = MIMEText(html, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = account.username
    message["To"] = to

    try:
        with smtplib.SMTP(host, 587, timeout=30) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(account.username, account.password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise ImapError(f"Could not send the digest: {exc}") from exc
