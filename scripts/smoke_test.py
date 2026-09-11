#!/usr/bin/env python3
"""Quick smoke test for glassboard overlay sizing and ink."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("GDK_BACKEND", "wayland")

from glassboard import _gi  # noqa: F401
from gi.repository import GLib, Gtk

from glassboard import input_region
from glassboard.overlay import OverlayWindow


def main() -> int:
    errs: list[BaseException] = []
    win = OverlayWindow()
    win.show_all()
    GLib.idle_add(win._ensure_toolbar_placed)

    def check() -> bool:
        try:
            def verify() -> bool:
                try:
                    w = win.get_allocated_width()
                    h = win.get_allocated_height()
                    print(f"window={w}x{h}", flush=True)
                    assert w > 100 and h > 100

                    region = input_region._last_region
                    print(f"click_region={region}", flush=True)
                    assert region is not None
                    assert region[0] == win._toolbar.get_pos()[0]
                    assert region[2] >= 100

                    win._toolbar.set_draw_mode(True, emit=True)

                    def verify_draw() -> bool:
                        try:
                            print(f"draw_region={input_region._last_region}", flush=True)
                            assert input_region._last_region is not None
                            assert input_region._last_region[2] >= 1000
                            assert win._ink.begin_stroke(100, 100)
                            assert win._ink.continue_stroke(180, 140)
                            assert win._ink.end_stroke()
                            data = win._ink._surface.get_data()
                            stride = win._ink._surface.get_stride()
                            i = 100 * stride + 100 * 4
                            assert data[i + 3] != 0
                            print("ink_ok", flush=True)

                            # Buttons path: collapse keeps mode
                            mode = win._toolbar.is_draw_mode()
                            win._toolbar.toggle_collapsed()

                            def verify_collapse() -> bool:
                                try:
                                    assert win._toolbar.is_collapsed()
                                    assert win._toolbar.is_draw_mode() == mode
                                    print("PASS", flush=True)
                                except BaseException as exc:
                                    print("FAIL", exc, flush=True)
                                    errs.append(exc)
                                finally:
                                    win.destroy()
                                    Gtk.main_quit()
                                return False

                            GLib.timeout_add(150, verify_collapse)
                        except BaseException as exc:
                            print("FAIL", exc, flush=True)
                            errs.append(exc)
                            win.destroy()
                            Gtk.main_quit()
                        return False

                    GLib.timeout_add(150, verify_draw)
                except BaseException as exc:
                    print("FAIL", exc, flush=True)
                    errs.append(exc)
                    win.destroy()
                    Gtk.main_quit()
                return False

            GLib.timeout_add(150, verify)
        except BaseException as exc:
            print("FAIL", exc, flush=True)
            errs.append(exc)
            win.destroy()
            Gtk.main_quit()
        return False

    GLib.timeout_add(800, check)
    Gtk.main()
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
