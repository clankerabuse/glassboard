"""Transparent fullscreen gtk-layer-shell overlay window."""

from __future__ import annotations

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

from glassboard.canvas import InkBoard, Tool, brush_radius
from glassboard.input_region import (
    apply_input_update,
    reset_input_cache,
    schedule_input_update,
    set_full_input,
)
from glassboard.toolbar import Toolbar


class OverlayWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Glassboard")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)

        self._prepare_transparent_visual()
        self._init_layer_shell()

        # Ink is painted on the window itself — no fullscreen child widget that
        # can steal clicks from the toolbar.
        self._ink = InkBoard()
        self._ink.on_changed = self.queue_draw

        self._overlay = Gtk.Overlay()
        self._overlay.set_app_paintable(True)
        # No-window filler so Gtk.Overlay has a main child; events fall through
        # to this toplevel where we handle drawing.
        filler = Gtk.Box()
        filler.set_hexpand(True)
        filler.set_vexpand(True)
        self._overlay.add(filler)

        self._toolbar = Toolbar(
            on_mode=self._set_draw_mode,
            on_tool=self._set_tool,
            on_color=self._set_color,
            on_width=self._set_width,
            on_undo=self._undo,
            on_clear=self._clear,
            on_quit=self.close,
            on_moved=self._on_toolbar_moved,
            on_drag_begin=self._on_toolbar_drag_begin,
            on_drag_end=self._on_toolbar_drag_end,
            on_layout_changed=self._on_toolbar_layout_changed,
        )
        self._toolbar.set_halign(Gtk.Align.START)
        self._toolbar.set_valign(Gtk.Align.START)
        self._overlay.add_overlay(self._toolbar)

        self.add(self._overlay)

        self._cursor_pos: tuple[float, float] | None = None

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )

        self.connect("draw", self._on_window_draw)
        self.connect("size-allocate", self._on_window_allocate)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("touch-event", self._on_touch)
        self.connect("key-press-event", self._on_key_press)
        self.connect("realize", self._on_realize)
        self.connect("map-event", self._on_map)
        self.connect("destroy", Gtk.main_quit)

        self._toolbar_placed = False
        self._set_draw_mode(False)

    def _prepare_transparent_visual(self) -> None:
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)

    def _init_layer_shell(self) -> None:
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self, "glassboard")
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(self, edge, True)
            GtkLayerShell.set_margin(self, edge, 0)
        GtkLayerShell.set_exclusive_zone(self, -1)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)

    def _undo(self) -> None:
        self._ink.undo()
        self.queue_draw()

    def _clear(self) -> None:
        self._ink.clear()
        self.queue_draw()

    def _set_tool(self, tool: Tool) -> None:
        self._ink.set_tool(tool)
        self.queue_draw()

    def _set_color(self, name: str) -> None:
        self._ink.set_color(name)
        self.queue_draw()

    def _set_width(self, width: float) -> None:
        self._ink.set_width(width)
        self.queue_draw()

    def _show_eraser_cursor(self) -> bool:
        return (
            self._ink.draw_enabled
            and self._ink.tool is Tool.ERASER
            and self._cursor_pos is not None
            and not self._event_over_toolbar(*self._cursor_pos)
        )

    def _cursor_pad(self) -> float:
        return brush_radius(self._ink.stroke_width()) + 3.0

    def _invalidate_cursor(self, *positions: tuple[float, float] | None) -> None:
        pad = self._cursor_pad()
        for pos in positions:
            if pos is None:
                continue
            x, y = pos
            self.queue_draw_area(
                int(x - pad),
                int(y - pad),
                int(pad * 2) + 2,
                int(pad * 2) + 2,
            )

    def _paint_eraser_cursor(self, cr: cairo.Context) -> None:
        if not self._show_eraser_cursor() or self._cursor_pos is None:
            return
        x, y = self._cursor_pos
        # Same radius the eraser actually clears (Cairo line_width / 2).
        radius = brush_radius(self._ink.stroke_width())
        # Exact footprint tint.
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.12)
        cr.arc(x, y, radius, 0, 2 * 3.14159265)
        cr.fill()
        # Hairline on the true edge (1px stroke, not a thick ring that
        # visually inflates the size).
        cr.set_line_width(1.0)
        cr.set_source_rgba(0.05, 0.05, 0.08, 0.65)
        cr.arc(x, y, radius, 0, 2 * 3.14159265)
        cr.stroke()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
        cr.arc(x, y, radius, 0, 2 * 3.14159265)
        cr.stroke()

    def _on_realize(self, *_args) -> None:
        self._refresh_input_region()

    def _on_map(self, *_args) -> bool:
        GLib.idle_add(self._ensure_toolbar_placed)
        return False

    def _on_window_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        self._ink.paint(cr)
        self._paint_eraser_cursor(cr)
        return False

    def _on_window_allocate(self, _widget: Gtk.Widget, allocation) -> None:
        self._ink.resize(allocation.width, allocation.height)
        if allocation.width > 1 and allocation.height > 1 and self._toolbar_placed:
            self._apply_toolbar_margins(allocation.width, allocation.height)

    def _ensure_toolbar_placed(self) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w > 1 and h > 1:
            self._place_toolbar(w, h, force_default=not self._toolbar_placed)
            reset_input_cache()
            self._refresh_input_region()
        return False

    def _place_toolbar(
        self, win_w: int, win_h: int, *, force_default: bool = False
    ) -> None:
        nat = self._toolbar.get_preferred_size()[1]
        alloc_w = self._toolbar.get_allocated_width()
        alloc_h = self._toolbar.get_allocated_height()
        tw = alloc_w if alloc_w > 1 else max(nat.width, 1)
        th = alloc_h if alloc_h > 1 else max(nat.height, 40)

        if force_default or not self._toolbar_placed:
            x = max(8, (win_w - tw) // 2)
            y = max(8, win_h - th - 24)
            self._toolbar.set_pos(x, y)
            self._toolbar_placed = True
        else:
            x, y = self._toolbar.get_pos()
            x = min(max(0, x), max(0, win_w - tw))
            y = min(max(0, y), max(0, win_h - th))
            self._toolbar.set_pos(x, y)

        x, y = self._toolbar.get_pos()
        self._toolbar.set_margin_start(x)
        self._toolbar.set_margin_top(y)

    def _apply_toolbar_margins(self, win_w: int, win_h: int) -> None:
        tw = self._toolbar.get_allocated_width()
        th = self._toolbar.get_allocated_height()
        if tw <= 1 or th <= 1:
            nat = self._toolbar.get_preferred_size()[1]
            tw = max(nat.width, 40)
            th = max(nat.height, 40)
        x, y = self._toolbar.get_pos()
        x = min(max(0, x), max(0, win_w - tw))
        y = min(max(0, y), max(0, win_h - th))
        self._toolbar.set_pos(x, y)
        if self._toolbar.get_margin_start() != x:
            self._toolbar.set_margin_start(x)
        if self._toolbar.get_margin_top() != y:
            self._toolbar.set_margin_top(y)

    def _on_toolbar_moved(self) -> None:
        # Visual follow only — do not rebuild the input shape mid-drag.
        win_w = self.get_allocated_width()
        win_h = self.get_allocated_height()
        self._apply_toolbar_margins(win_w, win_h)

    def _on_toolbar_drag_begin(self) -> None:
        # Keep receiving pointer events even if the cursor leaves the toolbar.
        reset_input_cache()
        set_full_input(self)

    def _on_toolbar_drag_end(self) -> None:
        win_w = self.get_allocated_width()
        win_h = self.get_allocated_height()
        self._apply_toolbar_margins(win_w, win_h)
        reset_input_cache()
        # Apply immediately so the hit box matches the new bar position.
        apply_input_update(
            self,
            self._toolbar,
            draw_mode=self._toolbar.is_draw_mode(),
        )

    def _on_toolbar_layout_changed(self) -> None:
        def _after_resize() -> bool:
            win_w = self.get_allocated_width()
            win_h = self.get_allocated_height()
            if win_w > 1 and win_h > 1:
                self._apply_toolbar_margins(win_w, win_h)
            reset_input_cache()
            self._refresh_input_region()
            return False

        GLib.idle_add(_after_resize)

    def _set_draw_mode(self, enabled: bool) -> None:
        self._ink.set_draw_enabled(enabled)
        if not enabled:
            old = self._cursor_pos
            self._cursor_pos = None
            self._invalidate_cursor(old)
        if enabled:
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.EXCLUSIVE
            )
        else:
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.NONE
            )
        reset_input_cache()
        self._refresh_input_region()
        self.queue_draw()

    def _refresh_input_region(self) -> None:
        schedule_input_update(
            self,
            self._toolbar,
            draw_mode=self._toolbar.is_draw_mode(),
        )

    def _event_over_toolbar(self, x: float, y: float) -> bool:
        alloc = self._toolbar.get_allocation()
        coords = self._toolbar.translate_coordinates(self, 0, 0)
        if coords is None:
            ox, oy = self._toolbar.get_pos()
        else:
            ox, oy = coords
        return (
            ox <= x <= ox + alloc.width
            and oy <= y <= oy + alloc.height
        )

    def _event_coords(self, event) -> tuple[float, float]:
        """Map event position into this window's coordinate space.

        The floating toolbar has its own GdkWindow. Unhandled motion from it
        propagates to us with toolbar-local x/y; treating those as window
        coords paints the eraser preview near the top of the screen.
        """
        event_win = event.get_window()
        our_win = self.get_window()
        x = float(event.x)
        y = float(event.y)
        if event_win is None or our_win is None or event_win == our_win:
            return (x, y)
        win = event_win
        while win is not None and win != our_win:
            x, y = win.coords_to_parent(x, y)
            win = win.get_parent()
        return (x, y)

    def _set_cursor_pos(self, x: float, y: float) -> tuple[float, float] | None:
        """Store cursor pos, or clear it while the pointer is over the toolbar."""
        old = self._cursor_pos
        if self._event_over_toolbar(x, y):
            self._cursor_pos = None
        else:
            self._cursor_pos = (x, y)
        return old

    def _on_button_press(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        x, y = self._event_coords(event)
        self._set_cursor_pos(x, y)
        if event.button != 1 or not self._ink.draw_enabled:
            return False
        if self._event_over_toolbar(x, y):
            return False
        if self._ink.begin_stroke(x, y):
            self.queue_draw()
            return True
        return False

    def _on_button_release(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        x, y = self._event_coords(event)
        old = self._set_cursor_pos(x, y)
        if self._ink.draw_enabled and self._ink.tool is Tool.ERASER:
            self._invalidate_cursor(old, self._cursor_pos)
        if event.button != 1:
            return False
        if self._ink.end_stroke():
            self.queue_draw()
            return True
        return False

    def _on_motion(self, _w: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        x, y = self._event_coords(event)
        old = self._set_cursor_pos(x, y)
        handled = False
        if event.state & Gdk.ModifierType.BUTTON1_MASK:
            if self._cursor_pos is not None and self._ink.continue_stroke(x, y):
                self.queue_draw()
                handled = True
        elif self._ink.draw_enabled and self._ink.tool is Tool.ERASER:
            self._invalidate_cursor(old, self._cursor_pos)
        return handled

    def _on_enter(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        x, y = self._event_coords(event)
        old = self._set_cursor_pos(x, y)
        if self._ink.draw_enabled and self._ink.tool is Tool.ERASER:
            self._invalidate_cursor(old, self._cursor_pos)
        return False

    def _on_leave(self, _w: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        old = self._cursor_pos
        self._cursor_pos = None
        self._invalidate_cursor(old)
        return False

    def _on_touch(self, _w: Gtk.Widget, event: Gdk.EventTouch) -> bool:
        if not self._ink.draw_enabled:
            return False
        x, y = self._event_coords(event)
        if event.type == Gdk.EventType.TOUCH_BEGIN:
            self._set_cursor_pos(x, y)
            if self._event_over_toolbar(x, y):
                return False
            if self._ink.begin_stroke(x, y):
                self.queue_draw()
                return True
        elif event.type == Gdk.EventType.TOUCH_UPDATE:
            old = self._set_cursor_pos(x, y)
            if self._cursor_pos is not None and self._ink.continue_stroke(x, y):
                self.queue_draw()
                return True
            if self._ink.tool is Tool.ERASER:
                self._invalidate_cursor(old, self._cursor_pos)
        elif event.type in (Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
            if self._ink.end_stroke():
                self.queue_draw()
                return True
        return False

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        key = Gdk.keyval_name(event.keyval) or ""
        key = key.lower()
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)

        if key == "escape":
            if self._toolbar.is_draw_mode():
                self._toolbar.set_draw_mode(False, emit=True)
            else:
                self.close()
            return True
        if key == "d" and not ctrl:
            self._toolbar.set_draw_mode(not self._toolbar.is_draw_mode(), emit=True)
            return True
        if key == "e":
            self._toolbar._select_tool(Tool.ERASER)
            return True
        if key == "p":
            self._toolbar._select_tool(Tool.PEN)
            return True
        if key == "z" and ctrl:
            self._undo()
            return True
        if key in ("backspace", "delete") and ctrl:
            self._clear()
            return True
        return False


def run() -> None:
    GLib.set_prgname("glassboard")
    win = OverlayWindow()
    win.show_all()
    GLib.idle_add(win._ensure_toolbar_placed)
    Gtk.main()
