"""The parts of triage that must not be left to a language model.

Everything here was moved out of the prompt after the model got it wrong on a
real inbox. The division is deliberate: the model answers one question per email
("does this matter?") and code does the rest.

  counting          a 7B reported "6 action, 5 fyi, 3 noise" for a list holding
                    5/3/6. Counting rows is not a language task.
  sender attribution it labelled row 8 "Regal" when row 8 was AAA Northeast.
                    Verdicts are joined back by index so drift is impossible.
  topic grouping    asking for a second field alongside the category made it
                    abandon the output format entirely.
  the user's rules  a stated preference gets forgotten halfway through a long
                    prompt about as often as it is honoured.

Order matters, and it is the opposite of intuition: the user's own demotions are
applied LAST, after every "this looks important" guard, because a recruiter
writing "URGENT — W2 contract" trips several of them and the user still does not
want it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

ACTION, FYI, NOISE, UNSORTED = "ACTION", "FYI", "NOISE", "UNCLASSIFIED"

# Anything the user paid for, or that touches their own money, is never
# marketing. Plurals matter: \bticket\b does not match "Regal Tickets for ...",
# which is exactly the subject this guard exists to catch.
MONEY = re.compile(
    r"\b(tickets?|receipts?|orders?|invoices?|payments?|statements?|refunds?"
    r"|confirmations?|bookings?|reservations?|deliver(?:y|ies|ed)|shipp(?:ed|ing)"
    r"|fico|credit score|balances?|transactions?|transfers?|deposits?|withdrawals?"
    r"|sign ?in from|new device|overdue|declined|failed)\b",
    re.I,
)

# Topic grouping, so related mail reads together in the digest. Deterministic
# for the reason above. Order is significant — first match wins.
TOPICS: tuple[tuple[str, re.Pattern], ...] = (
    ("Tickets & bookings", re.compile(r"regal|ticket|booking|showtime|cinema", re.I)),
    ("Money & accounts", re.compile(
        r"bank|payment|invoice|statement|fico|credit|zelle|robinhood|sofi|experian"
        r"|transfer|refund|balance|\bcard\b|sign ?in", re.I)),
    ("Jobs & recruiters", re.compile(
        r"hiring|recruit|job alert|roles?\b|position|\bw2\b|c2c|full[- ]time|vacanc"
        r"|resume|candidate|indeed|monster|lensa|dice"
        r"|\b(?:data|cloud|software|azure|aws|gcp|devops|ml|ai|backend|frontend|senior|junior|lead)"
        r"\s+(?:engineer|developer|architect|analyst|scientist)\b", re.I)),
    ("Property & home", re.compile(r"zillow|rental|for rent|mortgage|apartment|realty|listing", re.I)),
    ("Shopping & offers", re.compile(r"sale|% off|deal|discount|coupon|save \$|bonus points", re.I)),
    ("Social & networking", re.compile(r"linkedin|connect|invitation|follower|network", re.I)),
)


def topic_of(text: str) -> str:
    for name, pattern in TOPICS:
        if pattern.search(text):
            return name
    return "Other"


@dataclass
class UserRules:
    """The user's own preferences, compiled once per run."""

    boost: Optional[re.Pattern] = None      # force to ACTION
    demote: Optional[re.Pattern] = None     # force to NOISE
    about_me: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, boost: str = "", demote: str = "", about_me: Optional[list[str]] = None) -> "UserRules":
        def compile_safe(pattern: str) -> Optional[re.Pattern]:
            # A user-entered rule must never crash the digest.
            if not pattern.strip():
                return None
            try:
                return re.compile(pattern, re.I)
            except re.error:
                return None

        return cls(compile_safe(boost), compile_safe(demote), list(about_me or []))


@dataclass
class Decision:
    category: str
    decided_by: str   # audit trail: which rule produced this, for "why is this here?"
    topic: str


def decide(sender: str, subject: str, model_verdict: Optional[str], rules: UserRules) -> Decision:
    """Turn one model verdict into the category the user actually sees."""
    haystack = f"{sender} {subject}"
    category = model_verdict or UNSORTED
    decided_by = "model" if model_verdict else "unclassified"

    # A receipt the model dismissed as marketing gets pulled back up — and so
    # does one it failed to classify at all. Burying a security alert because
    # the model skipped a line is the worst possible failure, so the guard
    # applies to UNSORTED too and errs toward showing it.
    if category in (NOISE, UNSORTED) and MONEY.search(subject):
        category, decided_by = FYI, "money-guard"

    # Things the user said always matter.
    if rules.boost and rules.boost.search(haystack):
        category, decided_by = ACTION, "your rule: always important"

    # Things the user said never matter. Last, so it beats every guard above —
    # a recruiter email can trip the money guard and still be unwanted.
    if rules.demote and rules.demote.search(haystack) and not (
        rules.boost and rules.boost.search(haystack)
    ):
        category, decided_by = NOISE, "your rule: not interested"

    return Decision(category, decided_by, topic_of(haystack))
