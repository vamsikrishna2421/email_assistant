"""The prompt, and the reasons it looks the way it does.

Every constraint here was added after watching a local model fail on a real
inbox. Worth reading before "simplifying" it:

* **One line per email, category only.** The first version asked for judgement,
  ranking and prose in one go. A 7B produced a self-contradictory digest — it
  named one email, then said none mattered, then emitted a stray "0", and
  silently dropped four ticket confirmations and a credit alert.
* **No sender in the output.** It once labelled row 8 "Regal" when row 8 was AAA
  Northeast. Emitting only the number and the category makes misattribution
  impossible, since the sender is rejoined by index afterwards.
* **No counting.** Asked for a summary line it reported "6 action, 5 fyi, 3
  noise" for a list containing 5/3/6.
* **Gmail's filing is a hint, never a verdict.** The whole point of the digest
  is catching what you would otherwise miss, and what you miss is what Gmail
  filed under Promotions or Spam.
* **The user's context goes first.** A preference stated at the end of a long
  prompt is honoured about as often as it is forgotten.
"""

from __future__ import annotations

from typing import Sequence


def _render(emails: Sequence) -> str:
    return "\n\n".join(
        f"[{n + 1}] From: {e.sender}\n"
        f"    Subject: {e.subject}\n"
        f"    Filed by Gmail as: {e.filed_as}\n"
        f"    Preview: {e.snippet}"
        for n, e in enumerate(emails)
    )


def build_prompt(emails: Sequence, about_me: Sequence[str] = ()) -> str:
    context = ""
    if about_me:
        lines = "\n".join(f"- {line}" for line in about_me)
        context = (
            "Context about the person whose inbox this is. This overrides your\n"
            "general instincts about what looks important:\n\n"
            f"{lines}\n\n"
        )

    return f"""{context}Classify every email below.

For EACH email output exactly one line, in the same order, in this format:

<number>. <CATEGORY>

Nothing else on the line. Not the sender, not the subject, not an explanation —
I already have those. Only the number and the category.

CATEGORY must be exactly one of:
  ACTION  - the person has to do something: a reply is expected, a payment or
            account needs attention, a deadline or appointment applies to them.
  FYI     - worth knowing but nothing to do: a confirmation, receipt, ticket,
            statement, delivery notice, or an alert about their own account.
  NOISE   - marketing, promotions, newsletters, social or recommendation
            notifications.

Rules:
- Every email gets exactly one line. Do not skip any. Do not merge them.
- Do not count anything and do not write a summary line.
- A receipt, ticket or confirmation is FYI, never NOISE — they paid for it.
- An alert about their own money, credit or account is FYI at minimum.
- "Filed by Gmail as" is a hint about where it landed, NOT the answer. Gmail
  routinely files real invoices, account alerts and personal mail under
  Promotions or Spam. Judge the content. A payment failure in Spam is still a
  payment failure. Equally, something in Primary can still be NOISE.
- Invent nothing. Use only what is in the subject and preview.
- Output only those lines. No preamble, no headings, no commentary.

EMAILS:
{_render(emails)}"""
