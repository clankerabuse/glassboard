"""Wayland/GDK input-shape helpers for click-through mode."""

from __future__ import annotations

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import GLib, Gtk

# Last applied region, used to avoid redundant compositor updates.
_last_region: tuple[int, int, int, int] | None = None
_pending_id: int = 0


def _apply_region(
    window: Gtk.Window, x: int, y: int, width: int, height: int
) -> None:
    global _last_region
    gdk_window = window.get_window()
    if gdk_window is None:
        return
    key = (int(x), int(y), max(0, int(width)), max(0, int(height)))
    if _last_region == key:
        return
    _last_region = key
    region = cairo.Region(
        cairo.RectangleInt(key[0], key[1], key[2], key[3])
    )
    gdk_window.input_shape_combine_region(region, 0, 0)


def _cancel_pending() -> None:
    global _pending_id
    if _pending_id:
        GLib.source_remove(_pending_id)
        _pending_id = 0


def set_full_input(window: Gtk.Window) -> None:
    """Accept pointer events across the entire window (draw mode / drag)."""
    # A deferred toolbar-only update must not clobber this mid-drag/menu.
    _cancel_pending()
    width = max(1, window.get_allocated_width())
    height = max(1, window.get_allocated_height())
    _apply_region(window, 0, 0, width, height)


def set_toolbar_only_input(window: Gtk.Window, toolbar: Gtk.Widget) -> None:
    """Pass clicks through everywhere except the toolbar's on-screen box."""
    tw = toolbar.get_allocated_width()
    th = toolbar.get_allocated_height()
    if tw <= 1 or th <= 1:
        nat = toolbar.get_preferred_size()[1]
        tw = max(nat.width, 1)
        th = max(nat.height, 1)

    if hasattr(toolbar, "get_pos"):
        ox, oy = toolbar.get_pos()
    else:
        ox = toolbar.get_margin_start()
        oy = toolbar.get_margin_top()

    if tw <= 0 or th <= 0:
        _apply_region(window, 0, 0, 0, 0)
        return

    _apply_region(window, int(ox), int(oy), int(tw), int(th))


def apply_input_update(
    window: Gtk.Window,
    toolbar: Gtk.Widget,
    draw_mode: bool,
) -> None:
    """Apply the input shape immediately (no debounce)."""
    _cancel_pending()
    if draw_mode:
        set_full_input(window)
    else:
        set_toolbar_only_input(window, toolbar)


def schedule_input_update(
    window: Gtk.Window,
    toolbar: Gtk.Widget,
    draw_mode: bool,
) -> None:
    """Debounced input-shape update after GTK finishes allocating widgets."""
    global _pending_id

    def _apply() -> bool:
        global _pending_id
        _pending_id = 0
        if draw_mode:
            set_full_input(window)
        else:
            set_toolbar_only_input(window, toolbar)
        return False

    if _pending_id:
        GLib.source_remove(_pending_id)
    _pending_id = GLib.timeout_add(50, _apply)


def reset_input_cache() -> None:
    global _last_region
    _last_region = None
