"""CLI entrypoint for Glassboard."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("GDK_BACKEND", "wayland")

    try:
        from glassboard import _gi  # noqa: F401
    except ValueError as exc:
        print(f"glassboard: missing GTK typelib: {exc}", file=sys.stderr)
        sys.exit(1)

    from glassboard.overlay import run

    run()


if __name__ == "__main__":
    main()
