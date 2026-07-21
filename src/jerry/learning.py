"""Turning "not interested" into a rule Jerry keeps.

The loop is deliberately conservative. One click does not rewrite how your inbox
is read — it records evidence, and a rule appears once the evidence is there.
That matters because the alternative is a system that lurches after every stray
click and that you stop trusting.

Two kinds of rule come out of feedback:

  sender rules   "never show me anything from Lensa again" — exact, safe, and
                 created from a single click, because a sender is unambiguous.
  phrase rules   "stop showing me recruiter mail" — inferred from repeated
                 feedback across *different* senders, and only once the same
                 signal has been seen more than once.

Everything a rule produces is visible and reversible in the dashboard, and the
feedback that produced it is never deleted, so a rule can be withdrawn without
losing the history that justified it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from .db import DigestItem, Feedback, Preference, get_session

# How many separate pieces of feedback before a *phrase* rule is created. A
# sender rule needs only one; a phrase is a generalisation and can be wrong.
PHRASE_THRESHOLD = 2

# Words that carry no signal on their own — a rule built from these would bury
# half the inbox.
STOPWORDS = {
    "the", "your", "you", "and", "for", "with", "from", "this", "that", "are",
    "our", "new", "now", "get", "has", "have", "was", "will", "can", "all",
    "out", "more", "just", "about", "into", "over", "how", "why", "what", "who",
    "when", "week", "today", "day", "time", "please", "hi", "hello", "re",
}

WORD = re.compile(r"[a-z][a-z0-9'’-]{2,}")


@dataclass
class Learned:
    """What changed, in words the dashboard can show the user."""

    created: list[str]
    message: str


def _sender_key(sender: str) -> str:
    """A stable, regex-safe fragment identifying a sender."""
    return re.escape(sender.strip()[:64])


def record(item_id: int, kind: str, note: Optional[str] = None) -> Learned:
    """Store one piece of feedback and apply whatever it justifies.

    `kind` is "less_important" (not interested) or "more_important" (always
    show). Anything else is stored as a note and changes nothing on its own.
    """
    session = get_session()
    try:
        item = session.get(DigestItem, item_id)
        if item is None:
            return Learned([], "That email is no longer in Jerry's history.")

        session.add(Feedback(item_id=item_id, kind=kind, note=note, applied=False))
        session.commit()

        if kind == "less_important":
            return _demote_sender(session, item)
        if kind == "more_important":
            return _boost_sender(session, item)
        return Learned([], "Noted.")
    finally:
        session.close()


def _existing(session, kind: str, value: str) -> Optional[Preference]:
    return (
        session.query(Preference)
        .filter_by(kind=kind, value=value)
        .one_or_none()
    )


def _demote_sender(session, item: DigestItem) -> Learned:
    created: list[str] = []
    pattern = _sender_key(item.sender)

    rule = _existing(session, "demote", pattern)
    if rule is None:
        session.add(Preference(kind="demote", value=pattern, source="learned", evidence=1))
        created.append(f"Stop showing mail from {item.sender}")
    else:
        rule.evidence += 1
        rule.active = True

    # Remove a contradicting boost, otherwise the two rules fight and the boost
    # wins — leaving the user clicking "not interested" with no visible effect.
    contradiction = _existing(session, "boost", pattern)
    if contradiction is not None:
        session.delete(contradiction)
        created.append(f"Removed the earlier 'always show {item.sender}' rule")

    phrase = _maybe_phrase_rule(session, "less_important", "demote")
    if phrase:
        created.append(phrase)

    session.commit()
    message = created[0] if created else f"Already ignoring mail from {item.sender}."
    return Learned(created, message)


def _boost_sender(session, item: DigestItem) -> Learned:
    created: list[str] = []
    pattern = _sender_key(item.sender)

    rule = _existing(session, "boost", pattern)
    if rule is None:
        session.add(Preference(kind="boost", value=pattern, source="learned", evidence=1))
        created.append(f"Always show mail from {item.sender}")
    else:
        rule.evidence += 1
        rule.active = True

    contradiction = _existing(session, "demote", pattern)
    if contradiction is not None:
        session.delete(contradiction)
        created.append(f"Removed the earlier 'ignore {item.sender}' rule")

    session.commit()
    message = created[0] if created else f"Already prioritising mail from {item.sender}."
    return Learned(created, message)


def _maybe_phrase_rule(session, feedback_kind: str, rule_kind: str) -> Optional[str]:
    """Generalise from repeated feedback, but only across different senders.

    Three complaints about one newsletter says something about that newsletter.
    Three complaints about three different senders that share a word says
    something about the word — and only the second is worth a phrase rule.
    """
    rows = (
        session.query(Feedback, DigestItem)
        .join(DigestItem, Feedback.item_id == DigestItem.id)
        .filter(Feedback.kind == feedback_kind)
        .all()
    )
    if len(rows) < PHRASE_THRESHOLD:
        return None

    by_word: dict[str, set[str]] = {}
    for _feedback, item in rows:
        words = {
            w for w in WORD.findall(item.subject.lower())
            if w not in STOPWORDS and len(w) > 3
        }
        for word in words:
            by_word.setdefault(word, set()).add(item.sender.lower())

    for word, senders in sorted(by_word.items(), key=lambda kv: -len(kv[1])):
        if len(senders) < PHRASE_THRESHOLD:
            continue
        pattern = rf"\b{re.escape(word)}\b"
        if _existing(session, rule_kind, pattern) is not None:
            continue
        session.add(Preference(
            kind=rule_kind, value=pattern, source="learned", evidence=len(senders),
        ))
        verb = "Stop showing" if rule_kind == "demote" else "Always show"
        return f'{verb} mail mentioning "{word}" (seen from {len(senders)} different senders)'
    return None


def describe(pref) -> str:
    """A plain-English version of a rule.

    Rules are stored as regexes because that is what the matcher needs, but a
    user should never be shown `Gemini\\ Notebook` or a pipe-separated
    alternation. Sender rules unescape cleanly; anything more complex is
    summarised by its alternatives rather than printed raw.
    """
    # Order matters: strip the \b word-boundary markers BEFORE unescaping,
    # otherwise \bw2\b unescapes to "bw2b" and looks like a typo to the user.
    plain = pref.value.replace(r"\b", "")
    plain = re.sub(r"\\(.)", r"\1", plain)          # undo re.escape

    # A single literal phrase — the shape every learned sender rule has.
    if not re.search(r"[|()\[\]?*+{}^$]", plain):
        verb = "Mail from" if pref.source == "learned" else "Anything mentioning"
        return f"{verb} “{plain.strip()}”"

    # An alternation: show a few of the words it looks for.
    words = [w for w in re.split(r"\|", plain)
             if w and not re.search(r"[()\[\]{}?*+^$]", w)][:4]
    words = [w.strip() for w in words if w.strip()]
    if words:
        more = "…" if len(re.split(r"\|", plain)) > len(words) else ""
        return "Anything mentioning " + ", ".join(f"“{w}”" for w in words) + more
    return "A pattern you added"


def summary() -> dict:
    """Everything Jerry has learned, for the dashboard."""
    session = get_session()
    try:
        prefs = session.query(Preference).order_by(Preference.created_at.desc()).all()
        counts = Counter(f.kind for f in session.query(Feedback).all())
        return {
            "about_me": [p for p in prefs if p.kind == "about_me"],
            "boost": [p for p in prefs if p.kind == "boost"],
            "demote": [p for p in prefs if p.kind == "demote"],
            "feedback_counts": dict(counts),
        }
    finally:
        session.close()


def forget(preference_id: int) -> bool:
    """Withdraw a rule. The feedback that created it is kept."""
    session = get_session()
    try:
        rule = session.get(Preference, preference_id)
        if rule is None:
            return False
        session.delete(rule)
        session.commit()
        return True
    finally:
        session.close()


def add_about_me(text: str) -> Preference:
    """Free-text context, injected into the model's prompt."""
    session = get_session()
    try:
        rule = Preference(kind="about_me", value=text.strip(), source="user")
        session.add(rule)
        session.commit()
        return rule
    finally:
        session.close()
