"""Asking the local model to sort a batch of emails, and surviving the answer.

Two hard-won constraints shape this file.

**Batch size.** Given 34 emails in one 12.5k-character prompt, a 7B model
stopped emitting the requested format and returned prose under markdown
headings — 33 of 34 came back unclassified. The same model handles twelve
without trouble. Volume was the variable, not the model, so the work is split
into batches of twelve and the model is asked the same small question repeatedly.

**Reasoning models.** qwen3.5 and friends emit a chain-of-thought before the
answer. On a CPU that is minutes of tokens which are then discarded: measured on
this hardware, the identical two-character reply took 324s with thinking and
1.4s without. `think: false` is sent unconditionally; models that do not reason
ignore it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .prompt import build_prompt
from .rules import ACTION, FYI, NOISE

ENDPOINT = "http://127.0.0.1:11434"   # IPv4 literal: Ollama does not listen on ::1
BATCH_SIZE = 12
VALID = {ACTION, FYI, NOISE}

# "<n>. CATEGORY" — tolerant of bullets, bold markers and trailing commentary,
# because models decorate output in ways no instruction reliably prevents.
# Leading decoration is greedy on purpose: models emit "- **2.** FYI",
# "* 2) ACTION", "**3.** NOISE". Anything before the number is noise to us.
LINE = re.compile(r"^[\s\-*#>]*\**\s*(\d+)\s*[.)]\s*\**\s*([A-Za-z]+)")

Progress = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


@dataclass
class Email:
    """The little Jerry needs to triage: never the body."""

    id: str
    sender: str
    subject: str
    snippet: str
    filed_as: str = "Primary"
    buried: str = ""


def _ask(model: str, prompt: str, timeout: int = 3600) -> str:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},   # triage should not vary between runs
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(
        f"{ENDPOINT}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response).get("message", {}).get("content", "")


def _parse(text: str, size: int) -> dict[int, str]:
    """Pull "<n>. CATEGORY" out of whatever the model actually wrote."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        match = LINE.match(line)
        if not match:
            continue
        index, word = int(match.group(1)), match.group(2).upper()
        # Out-of-range numbers are hallucinated; a real verdict cannot refer to
        # an email that was not in the batch.
        if word in VALID and 1 <= index <= size:
            out[index] = word
    return out


def batches(emails: Sequence[Email], size: int = BATCH_SIZE) -> Iterable[Sequence[Email]]:
    for start in range(0, len(emails), size):
        yield emails[start:start + size]


def classify(
    emails: Sequence[Email],
    model: str,
    about_me: Optional[list[str]] = None,
    progress: Progress = _noop,
) -> tuple[list[Optional[str]], float]:
    """Classify every email. Returns (verdict per email, seconds taken).

    A verdict of None means the model did not produce a usable line for that
    email. That is surfaced rather than guessed at: an email shown as unsorted
    is honest, an email silently dropped is not.
    """
    verdicts: list[Optional[str]] = [None] * len(emails)
    started = time.monotonic()
    groups = list(batches(emails))

    for number, group in enumerate(groups):
        progress(f"Reading your email… batch {number + 1} of {len(groups)}")
        prompt = build_prompt(group, about_me or [])
        try:
            answer = _ask(model, prompt)
        except (urllib.error.URLError, OSError, ValueError):
            # One failed batch must not lose the others; those emails stay None
            # and appear as unsorted rather than vanishing.
            continue
        offset = number * BATCH_SIZE
        for local, category in _parse(answer, len(group)).items():
            verdicts[offset + local - 1] = category

    return verdicts, time.monotonic() - started
