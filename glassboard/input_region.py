"""Wayland/GTK input-shape helpers for click-through mode.

Critical: always set the shape via Gtk.Widget.input_shape_combine_region(),
never Gdk.Window.input_shape_combine_region() alone.

GTK stores the app shape on the widget (quark_input_shape_info). Later
gtk_widget_update_input_shape() — e.g. on realize / CSD updates — re-applies
that stored shape. If we only poke the GdkWindow, GTK still thinks there is
no app shape and writes NULL, which Wayland treats as a full-surface input
region (invisible click sink over the whole desktop).
"""

from __future__ import annotations

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import GLib, Gtk

# Last applied region key. None = not yet applied.
# ("full",) = entire window accepts input.
# ("empty",) = no input (pass everything through).
# (x, y, w, h) = toolbar-only box.
_last_region: tuple | None = None
_pending_id: int = 0
_watchdog_id: int = 0


def _apply_widget_shape(window: Gtk.Window, region: cairo.Region | None) -> None:
    """Set the widget-level input shape so GTK cannot clobber it later."""
    # Gtk.Window is a Gtk.Widget with its own GdkWindow.
    window.input_shape_combine_region(region)
    # Wayland only honors wl_surface.set_input_region on the NEXT surface
    # commit, and GDK only commits when a frame is painted. An idle overlay
    # never paints, so a corrected region could sit client-side forever —
    # that is exactly the "randomly frozen while doing nothing" state.
    # Queue a 1px damage so a commit (with the new region) always happens.
    window.queue_draw_area(0, 0, 1, 1)


def _apply_region(
    window: Gtk.Window, x: int, y: int, width: int, height: int, *, force: bool = False
) -> None:
    global _last_region
    if window.get_window() is None:
        return
    key = (int(x), int(y), max(0, int(width)), max(0, int(height)))
    if not force and _last_region == key:
        return
    _last_region = key
    if key[2] <= 0 or key[3] <= 0:
        # Empty region → compositor hits nothing → full click-through.
        _apply_widget_shape(window, cairo.Region())
        return
    region = cairo.Region(cairo.RectangleInt(key[0], key[1], key[2], key[3]))
    _apply_widget_shape(window, region)


def _apply_full(window: Gtk.Window, *, force: bool = False) -> None:
    """Clear the input shape so the whole surface accepts pointer events."""
    global _last_region
    if window.get_window() is None:
        return
    key = ("full",)
    if not force and _last_region == key:
        return
    _last_region = key
    # NULL shape == default == full surface (wl_surface.set_input_region(nil)).
    _apply_widget_shape(window, None)


def _apply_empty(window: Gtk.Window, *, force: bool = False) -> None:
    """Accept no pointer events (safe default before the toolbar is placed)."""
    global _last_region
    if window.get_window() is None:
        return
    key = ("empty",)
    if not force and _last_region == key:
        return
    _last_region = key
    _apply_widget_shape(window, cairo.Region())


def _cancel_pending() -> None:
    global _pending_id
    if _pending_id:
        GLib.source_remove(_pending_id)
        _pending_id = 0


def set_full_input(window: Gtk.Window) -> None:
    """Accept pointer events across the entire window (draw mode / drag)."""
    _cancel_pending()
    _apply_full(window)


def set_passthrough_input(window: Gtk.Window) -> None:
    """Accept no pointer events (used until the toolbar has a real allocation)."""
    _cancel_pending()
    _apply_empty(window)


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
        _apply_empty(window)
        return

    _apply_region(window, int(ox), int(oy), int(tw), int(th))


def apply_input_update(
    window: Gtk.Window,
    toolbar: Gtk.Widget,
    draw_mode: bool,
    *,
    force: bool = False,
) -> None:
    """Apply the input shape immediately (no debounce)."""
    _cancel_pending()
    if force:
        reset_input_cache()
    if draw_mode:
        set_full_input(window)
    else:
        set_toolbar_only_input(window, toolbar)


def schedule_input_update(
    window: Gtk.Window,
    toolbar: Gtk.Widget,
    draw_mode: bool,
) -> None:
    """Input-shape update after GTK finishes allocating widgets.

    Uses a short idle+timeout so allocation/margins settle, but also applies
    immediately when possible so we are not left with GTK's default full-surface
    input region (set_input_region(nil)).
    """
    global _pending_id

    # Fast path: if the window is realized, assert the desired shape now.
    if window.get_window() is not None:
        if draw_mode:
            set_full_input(window)
        else:
            set_toolbar_only_input(window, toolbar)

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
    # Two frames of settle time for margin/allocation without the old 50ms
    # window where the desktop stayed click-blocked.
    _pending_id = GLib.timeout_add(16, _apply)


def start_input_watchdog(
    window: Gtk.Window,
    toolbar: Gtk.Widget,
    is_draw_mode,
    has_pointer_capture,
) -> None:
    """Periodically re-assert the input region for the current mode.

    Defends against any GTK/compositor path that clears or desyncs the
    surface input region (missed commits, surface remaps after hide/show,
    compositor restarts). Runs in Click AND Draw mode — a Draw-mode desync
    used to be permanent because nothing ever re-asserted full input.
    """
    global _watchdog_id
    stop_input_watchdog()

    def _tick() -> bool:
        if window.get_window() is None:
            return True
        try:
            if has_pointer_capture():
                # Grip drag owns the region; Toolbar's capture watchdog
                # guarantees this state cannot persist after button release.
                return True
            # Force re-apply even if our cache matches — the compositor/GTK
            # side may have diverged.
            apply_input_update(
                window, toolbar, draw_mode=is_draw_mode(), force=True
            )
        except Exception:
            pass
        return True

    _watchdog_id = GLib.timeout_add(1000, _tick)


def stop_input_watchdog() -> None:
    global _watchdog_id
    if _watchdog_id:
        GLib.source_remove(_watchdog_id)
        _watchdog_id = 0


def reset_input_cache() -> None:
    global _last_region
    _last_region = None
