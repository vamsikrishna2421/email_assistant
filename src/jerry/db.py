"""Jerry's memory: what it read, what it decided, and what you told it.

Two things here earn their place beyond plain storage.

`Digest`/`DigestItem` keep the full history, including the model's original
verdict *and* the final one after the deterministic rules ran. Keeping both is
what makes it possible to answer "why did this show up" and to tell an
improvement apart from a coincidence when the prompt or model changes.

`Feedback` and `Preference` are the learning loop. Feedback is what you said
about one email; a Preference is the durable rule derived from it. They are kept
separate so a rule can be reviewed, edited or removed without losing the history
that produced it, and so one careless click never silently rewrites the rules.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DB_PATH, ensure_dirs


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Connection(Base):
    """A connected account, e.g. a Gmail mailbox.

    Tokens are stored here rather than in a file so there is exactly one place
    holding credentials, with one set of permissions to get right.
    """

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))          # "gmail"
    account: Mapped[str] = mapped_column(String(320))          # the address
    access_token: Mapped[Optional[str]] = mapped_column(Text, default=None)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, default=None)
    expires_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), default=None)
    scopes: Mapped[str] = mapped_column(Text, default="")
    connected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[Optional[str]] = mapped_column(Text, default=None)

    __table_args__ = (UniqueConstraint("provider", "account", name="uq_provider_account"),)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.account}"

    def secret(self) -> Optional[str]:
        """The refresh token, from the Keychain, falling back to the column.

        The column is only ever populated on platforms without a Keychain, or
        briefly before migration runs.
        """
        from .secrets import load
        return load(self.key) or self.refresh_token

    def remember(self, refresh_token: Optional[str]) -> None:
        """Store a refresh token in the Keychain, never in the database."""
        if not refresh_token:
            return
        from .secrets import available, store
        if available() and store(self.key, refresh_token):
            self.refresh_token = None
        else:
            self.refresh_token = refresh_token   # no Keychain: degrade, do not lose

    @property
    def healthy(self) -> bool:
        return bool(self.secret()) and not self.last_error


class Digest(Base):
    """One run. Kept even when it found nothing, so gaps are visible."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    window_hours: Mapped[int] = mapped_column(Integer, default=6)
    model: Mapped[str] = mapped_column(String(64), default="")
    seconds: Mapped[float] = mapped_column(default=0.0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[int] = mapped_column(Integer, default=0)
    fyi: Mapped[int] = mapped_column(Integer, default=0)
    noise: Mapped[int] = mapped_column(Integer, default=0)
    unsorted: Mapped[int] = mapped_column(Integer, default=0)
    rescued: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)

    items: Mapped[list["DigestItem"]] = relationship(
        back_populates="digest", cascade="all, delete-orphan", order_by="DigestItem.position",
    )


class DigestItem(Base):
    """One email as it appeared in one digest."""

    __tablename__ = "digest_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("digests.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    message_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    # Where the provider had filed it: Promotions, Spam, Primary...
    filed_as: Mapped[str] = mapped_column(String(32), default="")

    # The model's own verdict, before any rule ran.
    model_category: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    # What the user actually saw, after preferences and guards applied.
    category: Mapped[str] = mapped_column(String(16), default="UNCLASSIFIED")
    # Which rule changed it, if any — the audit trail for "why is this here".
    decided_by: Mapped[str] = mapped_column(String(48), default="model")
    topic: Mapped[str] = mapped_column(String(48), default="Other")

    digest: Mapped[Digest] = relationship(back_populates="items")
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
    )


class Feedback(Base):
    """Something the user said about one email.

    Kept forever and separately from the rules it produces, so a rule can be
    undone without losing the evidence, and so the same correction arriving
    three times can be told apart from one arriving once.
    """

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("digest_items.id", ondelete="CASCADE"), default=None, index=True,
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # "more_important" | "less_important" | "never_again" | "always_show" | "note"
    kind: Mapped[str] = mapped_column(String(32))
    # Free text, when the user explains rather than clicks.
    note: Mapped[Optional[str]] = mapped_column(Text, default=None)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)

    item: Mapped[Optional[DigestItem]] = relationship(back_populates="feedback")


class Preference(Base):
    """A durable rule about what matters to this person.

    `kind` decides how it is used:
      about_me  free text injected into the model's prompt as context
      boost     regex; matches are forced to NEEDS YOU
      demote    regex; matches are forced to IGNORED

    The regex kinds are enforced in code rather than left to the model, because
    a small model forgets a preference stated once in a long prompt roughly as
    often as it remembers it.
    """

    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="user")   # user | learned | default
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # How many separate pieces of feedback support this rule. Shown in the UI so
    # a rule inferred from a single click is not mistaken for a settled fact.
    evidence: Mapped[int] = mapped_column(Integer, default=1)


_engine = None
_Session = None


def session_factory():
    global _engine, _Session
    if _Session is None:
        ensure_dirs()
        _engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _Session


def get_session():
    return session_factory()()
