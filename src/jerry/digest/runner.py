"""One digest, start to finish: fetch, classify, decide, store, deliver.

Everything is recorded even when it fails. A digest that errored is a row with
an error on it, not an absence — because the failure mode that matters most for
a scheduled job is the silent one, where nothing arrives and a quiet inbox looks
exactly like a broken pipeline.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from ..config import Settings
from ..db import Digest, DigestItem, Preference, get_session
from .classify import Email, classify
from .render import Item, render, render_text
from .rules import ACTION, FYI, NOISE, UNSORTED, UserRules, decide

Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


def load_rules(session) -> UserRules:
    """Assemble the user's rules from everything they have told Jerry."""
    prefs = session.query(Preference).filter_by(active=True).all()
    about = [p.value for p in prefs if p.kind == "about_me"]

    def joined(kind: str) -> str:
        # Each stored rule is its own alternative, so one bad pattern cannot
        # swallow the others and every rule stays independently removable.
        parts = [p.value.strip() for p in prefs if p.kind == kind and p.value.strip()]
        return "|".join(f"(?:{p})" for p in parts)

    return UserRules.build(boost=joined("boost"), demote=joined("demote"), about_me=about)


def run_digest(
    fetch: Callable[[int], list[Email]],
    settings: Optional[Settings] = None,
    progress: Progress = _noop,
    deliver: Optional[Callable[[str, str], None]] = None,
) -> Digest:
    """Run one digest.

    `fetch` takes a window in hours and returns emails — passed in rather than
    imported so the engine does not care whether the mail came from Gmail, IMAP
    or a test fixture.
    `deliver` takes (subject, html). Omitted means store it without sending.
    """
    settings = settings or Settings.load()
    session = get_session()
    record = Digest(
        window_hours=settings.digest_hours,
        model=settings.model,
        created_at=dt.datetime.now(dt.timezone.utc),
    )

    try:
        progress("Checking your mail…")
        emails = fetch(settings.digest_hours)
        record.total = len(emails)

        rules = load_rules(session)

        verdicts: list[Optional[str]] = []
        seconds = 0.0
        if emails:
            verdicts, seconds = classify(
                emails, settings.model, about_me=rules.about_me, progress=progress,
            )
        record.seconds = seconds

        progress("Sorting…")
        items: list[Item] = []
        for position, (email, verdict) in enumerate(zip(emails, verdicts or [None] * len(emails))):
            decision = decide(email.sender, email.subject, verdict, rules)
            row = DigestItem(
                position=position,
                message_id=email.id,
                sender=email.sender,
                subject=email.subject,
                filed_as=email.filed_as,
                model_category=verdict,
                category=decision.category,
                decided_by=decision.decided_by,
                topic=decision.topic,
            )
            record.items.append(row)
            items.append(Item(
                sender=email.sender, subject=email.subject,
                category=decision.category, topic=decision.topic,
                buried=email.buried, decided_by=decision.decided_by,
            ))

        record.action = sum(1 for i in items if i.category == ACTION)
        record.fyi = sum(1 for i in items if i.category == FYI)
        record.noise = sum(1 for i in items if i.category == NOISE)
        record.unsorted = sum(1 for i in items if i.category == UNSORTED)
        record.rescued = sum(1 for i in items if i.buried and i.category in (ACTION, FYI))

        session.add(record)
        session.commit()

        # Items only have ids after the commit, and the dashboard's feedback
        # links need them.
        for item, row in zip(items, record.items):
            item.item_id = row.id

        html = render(items, hours=settings.digest_hours, model=settings.model)

        # Nothing worth reporting is not worth an email. A digest of pure noise
        # every six hours trains you to ignore it.
        worth_sending = record.action or record.fyi or record.unsorted
        if deliver and settings.deliver_email and worth_sending:
            progress("Sending your digest…")
            subject = (
                f"Inbox digest — {record.action} need you, {record.fyi} to know"
                if record.action or record.fyi else "Inbox digest"
            )
            deliver(subject, html)
            record.delivered = True
            session.commit()

        progress("Done.")
        return record

    except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
        record.error = f"{type(exc).__name__}: {exc}"[:500]
        session.add(record)
        session.commit()
        progress(f"Digest failed: {record.error}")
        return record
    finally:
        session.close()


def render_stored(digest_id: int, *, interactive: bool = True) -> str:
    """Re-render a stored digest, for the history browser."""
    session = get_session()
    try:
        record = session.get(Digest, digest_id)
        if record is None:
            return "<p>That digest no longer exists.</p>"
        items = [
            Item(
                sender=row.sender, subject=row.subject, category=row.category,
                topic=row.topic, decided_by=row.decided_by, item_id=row.id,
                buried=row.filed_as if row.filed_as in
                ("Spam", "Trash", "Promotions", "Social") else "",
            )
            for row in record.items
        ]
        return render(items, hours=record.window_hours, model=record.model,
                      interactive=interactive)
    finally:
        session.close()
