"""Shared chrome for the dashboard.

Markup is written in Python rather than template files for one practical reason:
a PyInstaller .app bundle has to be told about every data file it ships, and
templates that go missing fail at runtime in front of the user rather than at
build time in front of us. The digest itself is rendered by digest/render.py and
dropped in here unchanged, so the email and the dashboard always look identical.
"""

from __future__ import annotations

import html

FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif"
INK = "#1a1a1a"
MUTED = "#8a8a8a"
LINE = "#e8e8e8"
ACCENT = "#c0392b"


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def page(title: str, body: str, *, active: str = "") -> str:
    def tab(href: str, label: str, key: str) -> str:
        on = key == active
        return (
            f'<a href="{href}" style="text-decoration:none;padding:7px 2px;margin-right:20px;'
            f'font:{"600" if on else "400"} 14px {FONT};'
            f'color:{INK if on else MUTED};'
            f'border-bottom:2px solid {ACCENT if on else "transparent"};">{label}</a>'
        )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Jerry</title>
</head>
<body style="margin:0;background:#f4f4f5;font-family:{FONT};color:{INK};">
<div style="max-width:760px;margin:0 auto;padding:26px 20px 60px;">
  <div style="display:flex;align-items:baseline;justify-content:space-between;">
    <div style="font:700 22px/1.2 {FONT};">Jerry</div>
    <div style="font:12px {FONT};color:{MUTED};">reads your email on this Mac</div>
  </div>
  <div style="margin:16px 0 22px;border-bottom:1px solid {LINE};">
    {tab('/', 'Digests', 'home')}
    {tab('/rules', 'What Jerry knows', 'rules')}
    {tab('/connect', 'Accounts', 'connect')}
    {tab('/settings', 'Settings', 'settings')}
  </div>
  {body}
</div>
</body></html>"""


def card(inner: str, *, pad: str = "18px 20px") -> str:
    return (
        f'<div style="background:#fff;border:1px solid {LINE};border-radius:8px;'
        f'padding:{pad};margin-bottom:14px;">{inner}</div>'
    )


def button(href: str, label: str, *, primary: bool = False, method: str = "get") -> str:
    style = (
        f'display:inline-block;text-decoration:none;font:600 13px {FONT};'
        f'padding:8px 14px;border-radius:6px;'
        + (f'background:{ACCENT};color:#fff;border:1px solid {ACCENT};'
           if primary else
           f'background:#fff;color:{INK};border:1px solid #d5d5d5;')
    )
    if method == "post":
        return (
            f'<form method="post" action="{href}" style="display:inline;margin-right:8px;">'
            f'<button type="submit" style="{style};cursor:pointer;">{esc(label)}</button></form>'
        )
    return f'<a href="{href}" style="{style};margin-right:8px;">{esc(label)}</a>'


def notice(text: str, *, tone: str = "info") -> str:
    colour = {"info": "#2b6cb0", "good": "#2f855a", "warn": "#b7791f", "bad": ACCENT}[tone]
    background = {"info": "#f2f7fc", "good": "#f1f8f3", "warn": "#fdf8ef", "bad": "#fdf3f2"}[tone]
    return (
        f'<div style="border-left:3px solid {colour};background:{background};'
        f'padding:11px 14px;border-radius:0 6px 6px 0;margin-bottom:14px;'
        f'font:13px/1.5 {FONT};color:#333;">{text}</div>'
    )


def empty(text: str) -> str:
    return (
        f'<div style="text-align:center;padding:46px 20px;color:{MUTED};'
        f'font:14px/1.6 {FONT};">{esc(text)}</div>'
    )
