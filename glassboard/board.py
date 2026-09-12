"""Solid whiteboard backdrop: work area only (screen minus panels/taskbar)."""

from __future__ import annotations

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import Gdk, Gtk, GtkLayerShell

BOARD_COLORS: dict[str, tuple[float, float, float, float]] = {
    "white": (0.96, 0.96, 0.94, 0.98),
    "black": (0.08, 0.08, 0.10, 0.98),
}
BOARD_COLOR_DEFAULT = "white"


class SolidBoardWindow(Gtk.Window):
    """Opaque layer-shell surface that fills the compositor work area.

    Uses exclusive_zone=0 so KWin/Plasma sizes it around the taskbar and other
    exclusive panels. Lives on Layer.TOP so the transparent glass Overlay
    (and its toolbar) stay above it.
    """

    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Glassboard Board")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self._color_name = BOARD_COLOR_DEFAULT

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_namespace(self, "glassboard-board")
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
            GtkLayerShell.set_margin(self, edge, 0)
        # 0 = occupy the work area only (respect exclusive zones like the taskbar).
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

        self.connect("draw", self._on_draw)
        # Swallow pointer events so clicks don't fall through to apps
        # when the glass overlay is in Click (toolbar-only) mode.
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.connect("button-press-event", lambda *_: True)
        self.connect("button-release-event", lambda *_: True)
        self.connect("motion-notify-event", lambda *_: True)
        self.connect("touch-event", lambda *_: True)

    def set_color(self, name: str) -> None:
        if name not in BOARD_COLORS:
            return
        if self._color_name == name:
            return
        self._color_name = name
        self.queue_draw()

    def color_name(self) -> str:
        return self._color_name

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        r, g, b, a = BOARD_COLORS.get(
            self._color_name, BOARD_COLORS[BOARD_COLOR_DEFAULT]
        )
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(r, g, b, a)
        cr.paint()
        return False
