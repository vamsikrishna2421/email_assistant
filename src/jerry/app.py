"""The menu-bar app: the only part of Jerry the user meets first.

It does three things and stays out of the way — starts the local dashboard,
keeps the schedule ticking, and makes sure the AI is set up without ever saying
the word "Ollama".

First run is the one moment that decides whether someone keeps the app, so it is
sequenced to put something useful on screen before any long download starts:
the browser opens on the dashboard immediately, and the AI engine installs in
the background while the user connects their mailbox.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import Optional

from PIL import Image, ImageDraw

from . import ai_setup
from .config import HOST, PORT, Settings, ensure_dirs
from .db import Connection, get_session
from .mail import digest_now

DASHBOARD = f"http://{HOST}:{PORT}"


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------

def _icon(colour: str = "#2c2c2c") -> Image.Image:
    """A small envelope. Drawn rather than shipped so there is no asset to lose."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([8, 16, 56, 48], radius=5, outline=colour, width=5)
    draw.line([10, 19, 32, 36, 54, 19], fill=colour, width=5, joint="curve")
    return image


class Jerry:
    def __init__(self) -> None:
        ensure_dirs()
        # Any token still in the database from an earlier version moves to the
        # Keychain before anything reads it.
        try:
            from .secrets import migrate_from_database
            migrate_from_database()
        except Exception:      # noqa: BLE001 - never block startup on this
            pass
        self.settings = Settings.load()
        self.status = "Starting…"
        self.busy = False
        self._icon = None

    # -- state the menu reads ------------------------------------------------

    def connected_account(self) -> Optional[str]:
        session = get_session()
        try:
            row = session.query(Connection).filter_by(provider="gmail").first()
            return row.account if row else None
        finally:
            session.close()

    def _set(self, message: str) -> None:
        self.status = message
        if self._icon is not None:
            self._icon.title = f"Jerry — {message}"

    # -- background work -----------------------------------------------------

    def _serve_dashboard(self) -> None:
        from .server import serve
        serve()

    def _prepare_ai(self) -> None:
        """Install and warm the local model without blocking the first screen."""
        model = self.settings.model
        if ai_setup.have_model(model) and ai_setup.server_up():
            self._set("Ready")
            return
        self._set("Setting up…")
        ok, message = ai_setup.ensure_ready(model, progress=self._set)
        self._set("Ready" if ok else message)

    def _schedule(self) -> None:
        """Run a digest every N hours, and never let one failure end the loop."""
        while True:
            time.sleep(max(1, self.settings.digest_hours) * 3600)
            self.settings = Settings.load()
            if self.connected_account() is None:
                continue
            self.run_now()

    def run_now(self, *_args) -> None:
        if self.busy:
            return
        if self.connected_account() is None:
            self._set("Connect a mailbox first")
            webbrowser.open(f"{DASHBOARD}/connect")
            return

        def work() -> None:
            self.busy = True
            try:
                record = digest_now(self.settings, progress=self._set)
                if record.error:
                    self._set("Last run failed — open Jerry")
                else:
                    self._set(f"{record.action} need you · {record.fyi} to know")
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    # -- menu ----------------------------------------------------------------

    def _menu(self):
        import pystray

        def open_dashboard(*_a):
            webbrowser.open(DASHBOARD)

        def open_rules(*_a):
            webbrowser.open(f"{DASHBOARD}/rules")

        return pystray.Menu(
            pystray.MenuItem(lambda _i: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Read my inbox now", self.run_now),
            pystray.MenuItem("Open Jerry", open_dashboard, default=True),
            pystray.MenuItem("What Jerry knows", open_rules),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, _i: icon.stop()),
        )

    def run(self) -> None:
        import pystray

        threading.Thread(target=self._serve_dashboard, daemon=True).start()
        threading.Thread(target=self._prepare_ai, daemon=True).start()
        threading.Thread(target=self._schedule, daemon=True).start()

        first_time = not self.settings.onboarded
        if first_time:
            # Something useful on screen before the 4.7 GB download matters.
            self.settings.onboarded = True
            self.settings.save()
            threading.Timer(1.5, lambda: webbrowser.open(f"{DASHBOARD}/connect")).start()

        self._icon = pystray.Icon("jerry", _icon(), "Jerry", self._menu())
        self._set("Ready" if self.connected_account() else "Connect a mailbox")
        self._icon.run()


def main() -> None:
    Jerry().run()
