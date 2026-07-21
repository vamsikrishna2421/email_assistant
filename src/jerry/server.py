"""The local dashboard: history, feedback, accounts, settings.

Bound to loopback only. It holds a live mail token and has no authentication,
which is safe exactly as long as nothing outside this Mac can reach it.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Optional

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import ai_setup, learning
from .config import DEFAULT_TIER, HOST, PORT, TIERS, TIERS_BY_KEY, Settings
from .connectors import gmail
from .connectors import imap as imap_connector
from .db import Connection, Digest, get_session
from .digest.runner import render_stored, run_digest
from .mail import digest_now, fetch_for_digest
from .web.ui import button, card, empty, esc, notice, page

app = FastAPI(title="Jerry", docs_url=None, redoc_url=None)

# A digest takes minutes on CPU, so it runs off the request thread and the page
# reports progress instead of appearing to hang.
_state = {"running": False, "message": ""}


def _progress(message: str) -> None:
    _state["message"] = message


def _relative(when: dt.datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    minutes = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} min ago"
    if minutes < 48 * 60:
        return f"{int(minutes // 60)} h ago"
    return when.strftime("%d %b")


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home() -> str:
    session = get_session()
    try:
        digests = session.query(Digest).order_by(Digest.id.desc()).limit(30).all()
        connection = session.query(Connection).filter_by(provider="gmail").first()
    finally:
        session.close()

    blocks = []

    if connection is None:
        blocks.append(notice(
            'No mailbox connected yet. <a href="/connect">Connect one</a> and Jerry '
            'will start reading it for you.', tone="warn",
        ))

    if _state["running"]:
        blocks.append(notice(
            f'<b>Working…</b> {esc(_state["message"] or "starting")}'
            '<div style="margin-top:6px;color:#777;">This takes a few minutes — '
            'everything runs on your own Mac. You can close this page.</div>',
            tone="info",
        ))
    else:
        blocks.append(card(
            '<div style="display:flex;align-items:center;justify-content:space-between;">'
            '<div><div style="font:600 15px;">Read my inbox now</div>'
            f'<div style="font:13px;color:#888;margin-top:2px;">'
            f'Looks at the last {Settings.load().digest_hours} hours.</div></div>'
            + button("/run", "Run now", primary=True, method="post")
            + "</div>"
        ))

    if not digests:
        blocks.append(empty("No digests yet. Run one and it will appear here."))
    else:
        rows = []
        for d in digests:
            when = _relative(d.created_at)
            if d.error:
                summary = f'<span style="color:#c0392b;">failed — {esc(d.error[:70])}</span>'
            else:
                summary = (
                    f'<b style="color:#c0392b;">{d.action}</b> need you · '
                    f'<b style="color:#2b6cb0;">{d.fyi}</b> worth knowing · '
                    f'<span style="color:#999;">{d.noise} set aside</span>'
                    + (f' · <span style="color:#8e44ad;">{d.rescued} rescued</span>'
                       if d.rescued else "")
                )
            rows.append(
                f'<a href="/digest/{d.id}" style="display:block;text-decoration:none;'
                f'color:inherit;padding:11px 4px;border-bottom:1px solid #f0f0f0;">'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<div style="font:13px;">{summary}</div>'
                f'<div style="font:12px;color:#aaa;">{esc(when)}</div></div>'
                f'<div style="font:12px;color:#aaa;margin-top:3px;">'
                f'{d.total} emails · {d.seconds:.0f}s · {esc(d.model)}</div></a>'
            )
        blocks.append(card(
            '<div style="font:600 15px;margin-bottom:6px;">History</div>' + "".join(rows)
        ))

    return page("Digests", "".join(blocks), active="home")


@app.post("/run")
def run_now() -> RedirectResponse:
    if not _state["running"]:
        def work() -> None:
            _state["running"] = True
            try:
                digest_now(progress=_progress)
            finally:
                _state["running"] = False
                _state["message"] = ""
        threading.Thread(target=work, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/digest/{digest_id}", response_class=HTMLResponse)
def view_digest(digest_id: int) -> str:
    body = card(render_stored(digest_id, interactive=True), pad="4px 8px")
    return page("Digest", button("/", "← All digests") + "<div style='height:14px'></div>" + body,
                active="home")


@app.get("/feedback/{item_id}/{kind}")
def feedback(item_id: int, kind: str) -> RedirectResponse:
    mapping = {"less": "less_important", "more": "more_important"}
    if kind in mapping:
        learning.record(item_id, mapping[kind])
    return RedirectResponse("/rules?just_learned=1", status_code=303)


# ---------------------------------------------------------------------------
# What Jerry knows
# ---------------------------------------------------------------------------

@app.get("/rules", response_class=HTMLResponse)
def rules(just_learned: Optional[str] = None) -> str:
    knowledge = learning.summary()
    blocks = []

    if just_learned:
        blocks.append(notice("Thanks — Jerry has updated what it knows.", tone="good"))

    blocks.append(notice(
        "Jerry only learns from what you tell it, and every rule here can be removed. "
        "Nothing about your email is sent anywhere.", tone="info",
    ))

    def rule_list(title: str, items, kind_label: str) -> str:
        if not items:
            return (f'<div style="font:600 14px;margin:14px 0 6px;">{title}</div>'
                    f'<div style="font:13px;color:#999;">Nothing yet.</div>')
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:8px 0;border-bottom:1px solid #f2f2f2;">'
            f'<div><code style="font:12px ui-monospace,Menlo,monospace;background:#f6f6f6;'
            f'padding:2px 6px;border-radius:4px;">{esc(p.value[:70])}</code>'
            f'<div style="font:11px;color:#aaa;margin-top:3px;">'
            f'{esc(p.source)} · {p.evidence} time{"s" if p.evidence != 1 else ""}</div></div>'
            f'<a href="/rules/{p.id}/forget" style="font:12px;color:#999;'
            f'text-decoration:none;border:1px solid #e0e0e0;border-radius:4px;'
            f'padding:3px 9px;">Forget</a></div>'
            for p in items
        )
        return f'<div style="font:600 14px;margin:14px 0 6px;">{title}</div>{rows}'

    about = knowledge["about_me"]
    about_rows = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:12px;'
        f'padding:8px 0;border-bottom:1px solid #f2f2f2;">'
        f'<div style="font:13px/1.5;">{esc(p.value)}</div>'
        f'<a href="/rules/{p.id}/forget" style="font:12px;color:#999;text-decoration:none;'
        f'white-space:nowrap;">Forget</a></div>'
        for p in about
    ) or '<div style="font:13px;color:#999;">Nothing yet.</div>'

    blocks.append(card(
        '<div style="font:600 15px;">Things you have told Jerry</div>'
        '<div style="font:13px;color:#888;margin:4px 0 10px;">'
        'Plain sentences about what matters to you. Jerry reads these every time.</div>'
        + about_rows +
        '<form method="post" action="/rules/about" style="margin-top:12px;display:flex;gap:8px;">'
        '<input name="text" placeholder="e.g. I am not looking for a job" '
        'style="flex:1;padding:9px 11px;border:1px solid #ddd;border-radius:6px;font:13px inherit;">'
        '<button type="submit" style="padding:9px 15px;border-radius:6px;border:1px solid #c0392b;'
        'background:#c0392b;color:#fff;font:600 13px inherit;cursor:pointer;">Add</button></form>'
    ))

    blocks.append(card(
        rule_list("Always show", knowledge["boost"], "boost")
        + rule_list("Never show", knowledge["demote"], "demote")
    ))

    return page("What Jerry knows", "".join(blocks), active="rules")


@app.post("/rules/about")
def add_about(text: str = Form("")) -> RedirectResponse:
    if text.strip():
        learning.add_about_me(text)
    return RedirectResponse("/rules", status_code=303)


@app.get("/rules/{preference_id}/forget")
def forget_rule(preference_id: int) -> RedirectResponse:
    learning.forget(preference_id)
    return RedirectResponse("/rules", status_code=303)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@app.get("/connect", response_class=HTMLResponse)
def connect_page(error: Optional[str] = None) -> str:
    session = get_session()
    try:
        connections = session.query(Connection).all()
    finally:
        session.close()

    blocks = []
    if error:
        blocks.append(notice(esc(error), tone="bad"))

    if connections:
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:10px 0;border-bottom:1px solid #f2f2f2;">'
            f'<div><div style="font:600 14px;">{esc(c.account)}</div>'
            f'<div style="font:12px;color:{"#2f855a" if c.healthy else "#c0392b"};margin-top:2px;">'
            f'{"Connected" if c.healthy else esc(c.last_error or "Needs reconnecting")}</div></div>'
            f'{button(f"/connect/gmail", "Reconnect")}</div>'
            for c in connections
        )
        blocks.append(card('<div style="font:600 15px;margin-bottom:6px;">Your mailboxes</div>' + rows))
        blocks.append(card(
            '<div style="font:600 15px;">Add another mailbox</div>'
            '<div style="font:13px/1.6;color:#666;margin:6px 0 12px;">'
            'Jerry can watch more than one. Gmail connects in two clicks; anything '
            'else — Outlook, Yahoo, iCloud, Fastmail, your own server — uses an app '
            'password you create with that provider.</div>'
            + button("/connect/gmail", "Add Gmail")
            + button("/connect/imap", "Add another mailbox")
        ))
    else:
        blocks.append(card(
            '<div style="font:600 16px;">Connect your email</div>'
            '<div style="font:13px/1.6;color:#666;margin:8px 0 14px;">'
            'Jerry reads senders, subjects and previews — never the full message — '
            'and everything it reads stays on this Mac.</div>'
            '<div style="font:600 14px;margin-top:6px;">Gmail, the quick way</div>'
            '<div style="font:13px/1.6;color:#666;margin:3px 0 10px;">'
            'Two clicks. Google asks your permission in the browser.</div>'
            + button("/connect/gmail", "Connect Gmail", primary=True) +
            '<div style="border-top:1px solid #eee;margin:18px 0 0;padding-top:16px;">'
            '<div style="font:600 14px;">Any other mailbox</div>'
            '<div style="font:13px/1.6;color:#666;margin:3px 0 10px;">'
            'Outlook, Yahoo, iCloud, Fastmail or your own server — and Gmail too. '
            'Takes a few more steps: you create an app password with your provider '
            'and paste it here.</div>'
            + button("/connect/imap", "Set up another mailbox") + '</div>'
        ))

    return page("Accounts", "".join(blocks), active="connect")


@app.get("/connect/gmail")
def connect_gmail() -> RedirectResponse:
    try:
        tokens = gmail.connect()
    except gmail.GmailError as exc:
        return RedirectResponse(f"/connect?error={esc(str(exc))}", status_code=303)

    session = get_session()
    try:
        existing = (session.query(Connection)
                    .filter_by(provider="gmail", account=tokens.email).one_or_none())
        row = existing or Connection(provider="gmail", account=tokens.email)
        row.access_token = tokens.access_token
        row.expires_at = tokens.expires_at
        session.add(row)
        session.flush()          # `key` needs the account set before storing
        row.remember(tokens.refresh_token)
        row.scopes = " ".join(gmail.SCOPES)
        row.last_error = None
        session.add(row)
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/", status_code=303)


@app.get("/connect/imap", response_class=HTMLResponse)
def imap_form(provider: str = "gmail", error: Optional[str] = None) -> str:
    chosen = imap_connector.PROVIDERS.get(provider, imap_connector.PROVIDERS["gmail"])
    options = "".join(
        f'<option value="{k}" {"selected" if k == provider else ""}>{esc(v["label"])}</option>'
        for k, v in imap_connector.PROVIDERS.items()
    )
    field = ('padding:9px 11px;border:1px solid #ddd;border-radius:6px;'
             'font:13px inherit;width:100%;box-sizing:border-box;margin-bottom:9px;')

    blocks = []
    if error:
        blocks.append(notice(esc(error), tone="bad"))
    blocks.append(notice(
        f'<b>You need an app password, not your normal password.</b><br>{esc(chosen["help"])}',
        tone="warn",
    ))
    blocks.append(card(
        '<form method="post" action="/connect/imap">'
        '<div style="font:600 15px;margin-bottom:10px;">Your mailbox</div>'
        f'<select name="provider" onchange="location=\'/connect/imap?provider=\'+this.value" '
        f'style="{field}">{options}</select>'
        f'<input name="username" type="email" placeholder="you@example.com" required style="{field}">'
        f'<input name="password" type="password" placeholder="app password" required style="{field}">'
        + (f'<input name="host" placeholder="imap.example.com" style="{field}">'
           if not chosen["host"] else
           f'<input type="hidden" name="host" value="{esc(chosen["host"])}">')
        + f'<input type="hidden" name="port" value="{chosen["port"]}">'
        '<button type="submit" style="padding:9px 16px;border-radius:6px;border:1px solid #c0392b;'
        'background:#c0392b;color:#fff;font:600 13px inherit;cursor:pointer;margin-top:4px;">'
        'Connect</button></form>'
    ))
    return page("Connect a mailbox", "".join(blocks), active="connect")


@app.post("/connect/imap")
def connect_imap(provider: str = Form("gmail"), username: str = Form(...),
                 password: str = Form(...), host: str = Form(""),
                 port: int = Form(993)) -> RedirectResponse:
    account = imap_connector.Account(
        provider=provider, host=host or imap_connector.PROVIDERS[provider]["host"],
        port=port, username=username.strip(), password=password,
    )
    try:
        # Verify before storing: a saved-but-broken mailbox fails later, in the
        # background, where the user cannot see why.
        imap_connector.verify(account)
    except imap_connector.ImapError as exc:
        return RedirectResponse(
            f"/connect/imap?provider={provider}&error={esc(str(exc))}", status_code=303)

    session = get_session()
    try:
        existing = (session.query(Connection)
                    .filter_by(provider=f"imap:{provider}", account=account.username)
                    .one_or_none())
        row = existing or Connection(provider=f"imap:{provider}", account=account.username)
        # `scopes` carries host:port for IMAP; the password goes to the Keychain.
        row.scopes = f"{account.host}:{account.port}"
        row.last_error = None
        session.add(row)
        session.flush()
        row.remember(account.password)
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    settings = Settings.load()
    installed = ai_setup.installed_models()

    tiers = "".join(
        f'<label style="display:flex;gap:11px;align-items:flex-start;padding:11px;'
        f'border:1px solid {"#c0392b" if t.key == settings.tier else "#e6e6e6"};'
        f'border-radius:7px;margin-bottom:8px;cursor:pointer;">'
        f'<input type="radio" name="tier" value="{t.key}" '
        f'{"checked" if t.key == settings.tier else ""} style="margin-top:3px;">'
        f'<div><div style="font:600 14px;">{esc(t.label)}'
        f'<span style="font-weight:400;color:#999;"> · {esc(t.download)}</span>'
        + ('<span style="color:#2f855a;font-weight:400;"> · ready</span>'
           if any(m.split(":")[0] == t.model.split(":")[0] for m in installed)
           else '<span style="color:#b7791f;font-weight:400;"> · downloads on first use</span>')
        + f'</div><div style="font:13px;color:#777;margin-top:2px;">{esc(t.blurb)}</div>'
        f'</div></label>'
        for t in TIERS
    )

    body = card(
        '<form method="post" action="/settings">'
        '<div style="font:600 15px;margin-bottom:4px;">How carefully should Jerry read?</div>'
        f'<div style="font:13px;color:#888;margin-bottom:10px;">{esc(ai_setup.speed_note())}</div>'
        + tiers +
        '<div style="font:600 15px;margin:18px 0 6px;">How often?</div>'
        '<select name="hours" style="padding:8px 11px;border:1px solid #ddd;border-radius:6px;font:13px inherit;">'
        + "".join(
            f'<option value="{h}" {"selected" if settings.digest_hours == h else ""}>'
            f'Every {h} hours</option>' for h in (3, 6, 12, 24)
        )
        + "</select>"
        '<label style="display:flex;gap:9px;align-items:center;margin-top:16px;font:14px;">'
        f'<input type="checkbox" name="deliver" {"checked" if settings.deliver_email else ""}>'
        'Email the digest to me</label>'
        '<div style="margin-top:18px;">'
        '<button type="submit" style="padding:9px 16px;border-radius:6px;border:1px solid #c0392b;'
        'background:#c0392b;color:#fff;font:600 13px inherit;cursor:pointer;">Save</button></div>'
        '</form>'
    )
    return page("Settings", body, active="settings")


@app.post("/settings")
def save_settings(tier: str = Form(DEFAULT_TIER), hours: int = Form(6),
                  deliver: Optional[str] = Form(None)) -> RedirectResponse:
    settings = Settings.load()
    settings.tier = tier if tier in TIERS_BY_KEY else DEFAULT_TIER
    settings.digest_hours = int(hours)
    settings.deliver_email = deliver is not None
    settings.save()
    return RedirectResponse("/settings", status_code=303)


def serve() -> None:
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
