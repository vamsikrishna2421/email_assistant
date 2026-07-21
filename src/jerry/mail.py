"""Between the mailbox and the digest engine.

Kept separate so the engine never imports a provider: it is handed a fetch
function and does not care whether the mail came from Gmail, IMAP or a fixture.
That is what makes adding IMAP later a new file rather than a rewrite.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from .config import Settings
from .connectors import gmail
from .connectors import imap as imap_connector
from .db import Connection, Digest, get_session
from .digest.classify import Email
from .digest.runner import run_digest

SUBJECT_TAG = "Inbox digest"

Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


class NoMailbox(RuntimeError):
    pass


def _fresh_token(session, connection: Connection) -> str:
    """Return a usable access token, refreshing it if it has expired."""
    now = dt.datetime.now(dt.timezone.utc)
    expires = connection.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)

    if connection.access_token and expires and expires > now + dt.timedelta(minutes=2):
        return connection.access_token

    secret = connection.secret()
    if not secret:
        raise NoMailbox("That mailbox needs reconnecting.")

    tokens = gmail.refresh(secret)
    connection.access_token = tokens.access_token
    connection.expires_at = tokens.expires_at
    connection.remember(tokens.refresh_token)
    connection.last_error = None
    session.add(connection)
    session.commit()
    return tokens.access_token


def _imap_account(connection) -> imap_connector.Account:
    """Rebuild an IMAP account from the row plus the Keychain-held password."""
    host, _, port = connection.scopes.partition(":")   # scopes column reused as host:port
    return imap_connector.Account(
        provider=connection.provider.replace("imap:", ""),
        host=host, port=int(port or 993),
        username=connection.account, password=connection.secret() or "",
    )


def fetch_for_digest(hours: int) -> list[Email]:
    """Everything that arrived in the window, spam and promotions included."""
    session = get_session()
    try:
        connection = session.query(Connection).first()
        if connection is None:
            raise NoMailbox("No mailbox is connected yet.")

        if connection.provider.startswith("imap"):
            try:
                messages = imap_connector.fetch_recent(
                    _imap_account(connection), hours=hours, exclude_subject=SUBJECT_TAG,
                )
            except imap_connector.ImapError as exc:
                connection.last_error = str(exc)[:300]
                session.add(connection); session.commit()
                raise
            return [
                Email(id=m.id, sender=m.sender, subject=m.subject, snippet=m.snippet,
                      filed_as=m.filed_as, buried=m.buried)
                for m in messages
            ]

        token = _fresh_token(session, connection)

        try:
            messages = gmail.fetch_recent(
                token, hours=hours,
                # Without this the digest reads its own past output: each run
                # summarising the last one, real mail crowded out by reflections.
                exclude_subject=SUBJECT_TAG,
            )
        except gmail.GmailError as exc:
            connection.last_error = str(exc)[:300]
            session.add(connection)
            session.commit()
            raise

        return [
            Email(id=m.id, sender=m.sender, subject=m.subject, snippet=m.snippet,
                  filed_as=m.filed_as, buried=m.buried)
            for m in messages
        ]
    finally:
        session.close()


def _deliver(subject: str, html: str) -> None:
    session = get_session()
    try:
        connection = session.query(Connection).first()
        if connection is None:
            return
        if connection.provider.startswith("imap"):
            imap_connector.send_html(
                _imap_account(connection), to=connection.account,
                subject=subject, html=html,
            )
            return
        token = _fresh_token(session, connection)
        gmail.send_html(token, to=connection.account, subject=subject, html=html)
    finally:
        session.close()


def digest_now(settings: Optional[Settings] = None, progress: Progress = _noop) -> Digest:
    settings = settings or Settings.load()
    return run_digest(
        fetch=fetch_for_digest,
        settings=settings,
        progress=progress,
        deliver=_deliver,
    )
