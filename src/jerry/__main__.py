"""Entry point.

  jerry            menu-bar app (what the installed .app launches)
  jerry --serve    dashboard only, no tray — useful for development
  jerry --run      one digest, then exit — what a scheduled job would call
"""

from __future__ import annotations

import sys


def main() -> None:
    args = set(sys.argv[1:])

    if "--serve" in args:
        from .server import serve
        from .config import HOST, PORT
        print(f"Jerry dashboard on http://{HOST}:{PORT}")
        serve()
        return

    if "--run" in args:
        from .mail import digest_now
        record = digest_now(progress=lambda m: print(f"  {m}"))
        if record.error:
            print(f"failed: {record.error}")
            raise SystemExit(1)
        print(f"{record.action} need you · {record.fyi} worth knowing · {record.noise} set aside")
        return

    from .app import main as run_app
    run_app()


if __name__ == "__main__":
    main()
