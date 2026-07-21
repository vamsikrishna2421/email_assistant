"""Turning decided emails into something worth opening.

Inline styles only, everywhere. Gmail strips <style> blocks, so anything in a
stylesheet silently disappears and the digest arrives as unformatted text. The
same markup is used for the emailed digest and the dashboard, so there is one
place where the design lives.

Colour carries the priority — red needs you, blue worth knowing, grey ignored —
and within each band the mail is grouped by topic so related things read
together. A purple badge marks anything Gmail had buried in Spam or Promotions,
because "we found this where you would not have looked" is the digest's single
most valuable claim.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .rules import ACTION, FYI, NOISE, UNSORTED


@dataclass
class Item:
    sender: str
    subject: str
    category: str
    topic: str = "Other"
    buried: str = ""
    decided_by: str = "model"
    item_id: Optional[int] = None


_STYLE = {
    ACTION: ("#c0392b", "#fdf3f2", "Needs you", "#2c2c2c"),
    FYI: ("#2b6cb0", "#f2f7fc", "Worth knowing", "#2c2c2c"),
    UNSORTED: ("#b7791f", "#fdf8ef", "Couldn't sort these", "#2c2c2c"),
    NOISE: ("#b0b0b0", "#fafafa", "Not for you", "#8a8a8a"),
}

_FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _row(item: Item, kind: str, *, interactive: bool) -> str:
    accent, background, _label, colour = _STYLE[kind]
    badge = (
        f'<span style="display:inline-block;margin-left:8px;padding:1px 6px;border-radius:3px;'
        f'background:#8e44ad;color:#fff;font-size:10px;letter-spacing:.04em;">'
        f'was in {_esc(item.buried)}</span>'
    ) if item.buried else ""

    # Only the dashboard gets feedback controls; an emailed digest cannot have
    # working buttons, and a dead button is worse than none.
    controls = ""
    if interactive and item.item_id is not None:
        controls = (
            '<div style="margin-top:5px;font:11px ' + _FONT + ';">'
            f'<a href="/feedback/{item.item_id}/less" style="color:#8a8a8a;text-decoration:none;'
            'border:1px solid #ddd;border-radius:3px;padding:2px 7px;margin-right:5px;">'
            'Not interested</a>'
            f'<a href="/feedback/{item.item_id}/more" style="color:#8a8a8a;text-decoration:none;'
            'border:1px solid #ddd;border-radius:3px;padding:2px 7px;">'
            'Always show this</a>'
            f'<span style="color:#c8c8c8;margin-left:9px;">{_esc(item.decided_by)}</span>'
            '</div>'
        )

    return (
        f'<tr><td style="padding:7px 12px;border-left:3px solid {accent};background:{background};">'
        f'<div style="font:600 13px/1.4 {_FONT};color:{colour};">{_esc(item.sender)}{badge}</div>'
        f'<div style="font:13px/1.45 {_FONT};color:{colour};opacity:.85;">{_esc(item.subject)}</div>'
        f'{controls}</td></tr>'
    )


def _section(items: Sequence[Item], kind: str, *, interactive: bool) -> str:
    if not items:
        return ""
    accent, _bg, label, _c = _STYLE[kind]

    by_topic: dict[str, list[Item]] = {}
    for item in items:
        by_topic.setdefault(item.topic, []).append(item)

    blocks = "".join(
        f'<div style="font:600 11px/1 {_FONT};letter-spacing:.08em;text-transform:uppercase;'
        f'color:#7a7a7a;margin:16px 0 6px;">{_esc(topic)}'
        f'<span style="color:#b0b0b0;"> · {len(group)}</span></div>'
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:separate;border-spacing:0 3px;">'
        + "".join(_row(i, kind, interactive=interactive) for i in group)
        + "</table>"
        for topic, group in by_topic.items()
    )

    return (
        f'<div style="margin:26px 0 0;">'
        f'<div style="font:700 15px/1.3 {_FONT};color:{accent};'
        f'border-bottom:2px solid {accent};padding-bottom:5px;">{label} '
        f'<span style="font-weight:400;color:#9a9a9a;">({len(items)})</span></div>'
        f'{blocks}</div>'
    )


def _chip(count: int, label: str, colour: str) -> str:
    return (
        f'<span style="display:inline-block;margin-right:14px;font:13px {_FONT};color:#555;">'
        f'<b style="color:{colour};font-size:17px;">{count}</b> {label}</span>'
    )


def render(items: Iterable[Item], *, hours: int, model: str,
           interactive: bool = False) -> str:
    items = list(items)
    action = [i for i in items if i.category == ACTION]
    fyi = [i for i in items if i.category == FYI]
    noise = [i for i in items if i.category == NOISE]
    unsorted = [i for i in items if i.category == UNSORTED]
    rescued = [i for i in action + fyi if i.buried]

    if not items:
        body = (
            f'<div style="font:14px/1.6 {_FONT};color:#666;margin-top:18px;">'
            f'Nothing arrived in the last {hours} hours.</div>'
        )
    else:
        rescue_note = (
            f'<div style="margin-top:16px;padding:11px 13px;border-left:3px solid #8e44ad;'
            f'background:#f8f4fb;font:13px/1.5 {_FONT};color:#3a3a3a;">'
            f'<b>{len(rescued)} {"message was" if len(rescued) == 1 else "messages were"} '
            f'buried by Gmail</b> in Spam or Promotions but looks worth seeing. Marked below.'
            f'</div>'
        ) if rescued else ""

        tail = ""
        if noise:
            topics = list(dict.fromkeys(i.topic for i in noise))[:3]
            mostly = f" — mostly {', '.join(t.lower() for t in topics)}" if topics else ""
            tail = (
                f'<div style="margin:26px 0 0;font:12px/1.5 {_FONT};color:#a0a0a0;'
                f'border-top:1px solid #eee;padding-top:12px;">'
                f'Set aside {len(noise)}{mostly}.</div>'
            )

        body = (
            f'<div style="margin:16px 0 4px;padding:11px 13px;background:#f6f6f6;border-radius:5px;">'
            f'{_chip(len(action), "need you", _STYLE[ACTION][0])}'
            f'{_chip(len(fyi), "worth knowing", _STYLE[FYI][0])}'
            f'{_chip(len(noise), "set aside", "#9a9a9a")}'
            f'{_chip(len(unsorted), "unsorted", _STYLE[UNSORTED][0]) if unsorted else ""}'
            f'</div>'
            f'{rescue_note}'
            f'{_section(action, ACTION, interactive=interactive)}'
            f'{_section(fyi, FYI, interactive=interactive)}'
            f'{_section(unsorted, UNSORTED, interactive=interactive)}'
            f'{tail}'
        )

    return (
        f'<div style="max-width:640px;margin:0 auto;padding:22px;background:#ffffff;">'
        f'<div style="font:700 19px/1.2 {_FONT};color:#1a1a1a;">Inbox digest</div>'
        f'<div style="font:13px {_FONT};color:#8a8a8a;margin-top:3px;">'
        f'{len(items)} emails in the last {hours} hours</div>'
        f'{body}'
        f'<div style="margin-top:14px;font:11px {_FONT};color:#c0c0c0;">'
        f'Read on your own Mac by {_esc(model)}. Nothing left this machine.</div>'
        f'</div>'
    )


def render_text(items: Iterable[Item], *, hours: int) -> str:
    """Plain-text fallback for clients that refuse HTML."""
    items = list(items)
    action = [i for i in items if i.category == ACTION]
    fyi = [i for i in items if i.category == FYI]
    noise = [i for i in items if i.category == NOISE]

    lines = [f"{len(items)} emails in the last {hours} hours.", ""]
    for label, group in (("NEEDS YOU:", action), ("WORTH KNOWING:", fyi)):
        if group:
            lines.append(label)
            lines += [
                f"  - {i.sender} — {i.subject}" + (f"  [was in {i.buried}]" if i.buried else "")
                for i in group
            ]
            lines.append("")
    lines.append(f"Set aside {len(noise)} as marketing or newsletters.")
    return "\n".join(lines)
