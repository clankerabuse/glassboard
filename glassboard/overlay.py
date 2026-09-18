"""Transparent fullscreen gtk-layer-shell overlay window."""

from __future__ import annotations

import math

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell

from glassboard.board import SolidBoardWindow
from glassboard.canvas import (
    WIDTH_MIN,
    InkBoard,
    Tool,
    brush_radius,
    max_width_for_tool,
)
from glassboard.input_region import (
    apply_input_update,
    reset_input_cache,
    schedule_input_update,
    set_full_input,
    set_passthrough_input,
    start_input_watchdog,
    stop_input_watchdog,
)
from glassboard.toolbar import Toolbar

# Stable D-Bus / GApplication id so a second launch activates the first.
APPLICATION_ID = "com.glassboard.Glassboard"

# Stylus button-3 size gesture: frozen ring + tip marker.
_SIZE_POINTER_DOT_R = 3.0
_SIZE_POINTER_INVALIDATE_PAD = 8.0
# Tip travel before a button-3 press counts as a drag (not a preset click).
# Kept loose so stylus jitter / micro-moves still cycle presets.
_SIZE_GESTURE_CLICK_PX = 32.0
# Near-center soft zone: linear WIDTH_MIN..SOFT_MAX over this radius (easy 1px).
_SIZE_SOFT_ZONE_PX = 12.0
_SIZE_SOFT_ZONE_MAX = 8.0
# Outside the soft zone: power curve up to the active tool's max by this tip travel.
_SIZE_GESTURE_D_REF = 100.0
_SIZE_GESTURE_GAMMA = 2.2


def _width_from_size_gesture_dist(dist: float, width_max: float) -> float:
    """Map tip distance from the press point onto stroke width (soft zone + curve)."""
    dist = max(0.0, float(dist))
    width_max = max(WIDTH_MIN, float(width_max))
    soft = _SIZE_SOFT_ZONE_PX
    soft_max = min(_SIZE_SOFT_ZONE_MAX, width_max)
    if dist <= soft:
        # B: 0 → WIDTH_MIN, soft → soft_max (continuous linear).
        t = dist / soft if soft > 0 else 0.0
        return WIDTH_MIN + (soft_max - WIDTH_MIN) * t
    # A: continue from soft_max → width_max with a power curve.
    span = max(1.0, _SIZE_GESTURE_D_REF - soft)
    t = min(1.0, (dist - soft) / span)
    return soft_max + (width_max - soft_max) * (t ** _SIZE_GESTURE_GAMMA)


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application | None = None) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._app = application
        if application is not None:
            self.set_application(application)
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
            on_quit=self.quit_app,
            on_board=self._set_board_open,
            on_ink_visible=self._set_ink_visible,
            on_moved=self._on_toolbar_moved,
            on_drag_begin=self._on_toolbar_drag_begin,
            on_drag_end=self._on_toolbar_drag_end,
            on_layout_changed=self._on_toolbar_layout_changed,
            on_pointer_chrome=self._on_pointer_chrome,
        )
        self._toolbar.set_halign(Gtk.Align.START)
        self._toolbar.set_valign(Gtk.Align.START)
        self._overlay.add_overlay(self._toolbar)

        self.add(self._overlay)

        self._board = SolidBoardWindow()
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
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )

        self.connect("draw", self._on_window_draw)
        self.connect("size-allocate", self._on_window_allocate)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("touch-event", self._on_touch)
        self.connect("scroll-event", self._on_scroll)
        self.connect("key-press-event", self._on_key_press)
        # After the default realize handler: GdkWindow must exist, and we need
        # quark_input_shape_info set before/with GTK's own shape update.
        self.connect_after("realize", self._on_realize)
        self.connect("map-event", self._on_map)
        self.connect("destroy", self._on_destroy)

        self._toolbar_placed = False
        self._cursor_draw: Gdk.Cursor | None = None
        self._cursor_blank: Gdk.Cursor | None = None
        self._tray = None
        # Stylus button-3 press-drag size adjust (fixed preview at press point).
        self._size_anchor: tuple[float, float] | None = None
        self._size_pointer: tuple[float, float] | None = None
        self._size_dragged = False
        self._ink_visible = True
        self._set_draw_mode(False)

    def set_tray(self, tray) -> None:
        self._tray = tray

    def quit_app(self) -> None:
        """Fully exit (toolbar Quit / tray Quit)."""
        self.close()

    def hide_to_tray(self) -> None:
        """Dismiss the overlay but keep the process alive for the tray icon."""
        self._end_size_gesture()
        if self._toolbar.has_pointer_capture():
            self._toolbar.end_pointer_capture()
        if self._toolbar.is_board_open():
            self._toolbar.set_board_open(False, emit=True)
        if self._toolbar.is_draw_mode():
            self._toolbar.set_draw_mode(False, emit=True)
        if self._board.get_visible():
            self._board.hide()
        self.hide()
        if self._tray is not None:
            self._tray.sync_menu()

    def show_from_tray(self) -> None:
        """Restore the overlay after hide_to_tray()."""
        self.show_all()
        GLib.idle_add(self._ensure_toolbar_placed)
        if self._tray is not None:
            self._tray.sync_menu()

    def _on_destroy(self, *_args) -> None:
        stop_input_watchdog()
        if self._board.get_realized():
            self._board.destroy()
        if self._app is not None:
            self._app.quit()
        else:
            Gtk.main_quit()

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

    def _set_ink_visible(self, visible: bool) -> None:
        self._ink_visible = bool(visible)
        self.queue_draw()

    def _ensure_ink_visible(self) -> None:
        """Reveal markings when the user starts drawing while they are hidden."""
        if not self._ink_visible:
            self._toolbar.set_ink_visible(True, emit=True)

    def _set_tool(self, tool: Tool) -> None:
        self._ink.set_tool(tool)
        self._refresh_pointer_cursor()
        self.queue_draw()

    def _set_color(self, color: tuple[float, float, float, float]) -> None:
        self._ink.set_color(color)
        self._refresh_pointer_cursor()
        self.queue_draw()

    def _set_width(self, width: float) -> None:
        self._ink.set_width(width)
        self.queue_draw()

    def _on_scroll(self, _w: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if not self._ink.draw_enabled:
            return False
        x, y = self._event_coords(event)
        if self._event_over_toolbar(x, y):
            return False

        direction = event.direction
        if direction == Gdk.ScrollDirection.SMOOTH:
            dy = float(event.delta_y)
            if abs(dy) < 0.1:
                return False
            # Negative delta_y = wheel up → larger brush.
            delta = 1.0 if dy < 0 else -1.0
        elif direction == Gdk.ScrollDirection.UP:
            delta = 1.0
        elif direction == Gdk.ScrollDirection.DOWN:
            delta = -1.0
        else:
            return False

        self._toolbar.nudge_width(delta)
        return True

    def _show_brush_size_cursor(self) -> bool:
        """Size ring: eraser tool, or while the stylus size gesture is active."""
        if not self._ink.draw_enabled:
            return False
        if self._size_anchor is not None:
            return True
        return (
            self._ink.tool is Tool.ERASER
            and self._cursor_pos is not None
            and not self._event_over_toolbar(*self._cursor_pos)
        )

    def _brush_size_preview_pos(self) -> tuple[float, float] | None:
        """Where the size ring is drawn — frozen at the press point during resize."""
        if self._size_anchor is not None:
            return self._size_anchor
        if self._show_brush_size_cursor():
            return self._cursor_pos
        return None

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

    def _invalidate_size_pointer(self, *positions: tuple[float, float] | None) -> None:
        pad = _SIZE_POINTER_INVALIDATE_PAD
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

    def _paint_brush_size_cursor(self, cr: cairo.Context) -> None:
        pos = self._brush_size_preview_pos()
        if pos is None:
            return
        x, y = pos
        # Same radius the brush/eraser actually paints (Cairo line_width / 2).
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

        # Live stylus tip during resize — shows distance from the frozen ring.
        if self._size_anchor is not None and self._size_pointer is not None:
            px, py = self._size_pointer
            cr.set_source_rgba(0.05, 0.05, 0.08, 0.55)
            cr.arc(px, py, _SIZE_POINTER_DOT_R + 1.0, 0, 2 * 3.14159265)
            cr.fill()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            cr.arc(px, py, _SIZE_POINTER_DOT_R, 0, 2 * 3.14159265)
            cr.fill()
            cr.set_line_width(1.0)
            cr.set_source_rgba(0.05, 0.05, 0.08, 0.85)
            cr.arc(px, py, _SIZE_POINTER_DOT_R, 0, 2 * 3.14159265)
            cr.stroke()

    def _on_realize(self, *_args) -> None:
        # GTK's first buffer commit uses set_input_region(nil) (= full surface)
        # until a widget-level shape exists. Punch through immediately so we
        # never sit on a click-sink while the toolbar is still being placed.
        # Do NOT apply toolbar-only yet — the bar is still at (0,0) until
        # _ensure_toolbar_placed runs.
        display = self.get_display()
        self._cursor_draw = Gdk.Cursor.new_from_name(display, "crosshair")
        self._cursor_blank = Gdk.Cursor.new_from_name(display, "none")
        reset_input_cache()
        set_passthrough_input(self)
        start_input_watchdog(
            self,
            self._toolbar,
            is_draw_mode=self._toolbar.is_draw_mode,
            has_pointer_capture=self._toolbar.has_pointer_capture,
        )
        self._refresh_pointer_cursor()

    def _refresh_pointer_cursor(self) -> None:
        """Use a drawing crosshair (or blank over the size ring), not the arrow."""
        window = self.get_window()
        if window is None:
            return
        if not self._ink.draw_enabled:
            window.set_cursor(None)
            return
        # Over the toolbar (or left the surface): normal arrow for UI chrome.
        if self._cursor_pos is None:
            window.set_cursor(None)
            return
        if self._show_brush_size_cursor():
            # Painted size ring is the cursor; hide the system pointer.
            window.set_cursor(self._cursor_blank)
        else:
            window.set_cursor(self._cursor_draw)

    def _on_map(self, *_args) -> bool:
        GLib.idle_add(self._ensure_toolbar_placed)
        return False

    def _on_window_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        # Restore the default operator before anything else renders. GTK draws
        # the toolbar with this same context after we return; if it is left on
        # SOURCE, translucent widget backgrounds replace the surface instead
        # of blending, and the desktop bleeds through the bar.
        cr.set_operator(cairo.OPERATOR_OVER)
        if self._ink_visible:
            self._ink.paint(cr)
        self._paint_brush_size_cursor(cr)
        return False

    def _on_window_allocate(self, _widget: Gtk.Widget, allocation) -> None:
        self._ink.resize(allocation.width, allocation.height)
        if allocation.width > 1 and allocation.height > 1 and self._toolbar_placed:
            self._apply_toolbar_margins(allocation.width, allocation.height)
            # Re-assert the widget shape after every configure/allocate. GTK
            # may refresh CSD/input shapes around resize; our Gdk-only path
            # used to get wiped here and leave a full-screen click sink.
            if not self._toolbar.has_pointer_capture():
                apply_input_update(
                    self,
                    self._toolbar,
                    draw_mode=self._toolbar.is_draw_mode(),
                    force=True,
                )

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
            # Clear typical bottom panels (~40–54px); overlay is fullscreen.
            y = max(8, win_h - th - 56)
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
        # Grip press arms fullscreen input so Wayland keeps delivering
        # motion/release after the cursor leaves the toolbar window.
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

    def _on_pointer_chrome(self, over: bool) -> None:
        """Hide eraser size preview while the pointer is on the toolbar."""
        if over:
            old = self._cursor_pos
            if old is None:
                return
            self._cursor_pos = None
            self._invalidate_cursor(old)
            self._refresh_pointer_cursor()
        # Leaving chrome: next motion/enter on the glass restores the preview.

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
        self._refresh_pointer_cursor()
        self.queue_draw()

    def _set_board_open(self, open_: bool, color: str) -> None:
        self._board.set_color(color)
        if open_:
            self._board.show_all()
            # Solid board is most useful when you can ink immediately.
            if not self._toolbar.is_draw_mode():
                self._toolbar.set_draw_mode(True, emit=True)
        else:
            self._board.hide()

    def _refresh_input_region(self) -> None:
        if not self._toolbar_placed and not self._toolbar.is_draw_mode():
            # Safer than a wrong (0,0) strip or GTK's default full-surface sink.
            set_passthrough_input(self)
            return
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
        # Arrow over toolbar chrome; crosshair/blank out on the glass.
        if (old is None) != (self._cursor_pos is None):
            self._refresh_pointer_cursor()
        return old

    def _is_stylus_event(self, event) -> bool:
        """True for tablet pen / eraser tools (not mouse or touch)."""
        tool = event.get_device_tool()
        if tool is not None:
            return tool.get_tool_type() in (
                Gdk.DeviceToolType.PEN,
                Gdk.DeviceToolType.ERASER,
                Gdk.DeviceToolType.BRUSH,
                Gdk.DeviceToolType.PENCIL,
                Gdk.DeviceToolType.AIRBRUSH,
            )
        device = event.get_source_device() or event.get_device()
        if device is not None:
            return device.get_source() in (
                Gdk.InputSource.PEN,
                Gdk.InputSource.ERASER,
            )
        return False

    def _event_pressure(self, event) -> float | None:
        """Tablet pen pressure in 0..1, or None for mouse / missing axis.

        On Wayland the event's source device is often the master "pointer"
        (InputSource.MOUSE) even while a Wacom pen is drawing — pressure still
        arrives on the event via the tablet protocol. Trust the axis / device
        tool first; only then fall back to device source.
        """
        try:
            ok, value = event.get_axis(Gdk.AxisUse.PRESSURE)
        except (TypeError, AttributeError):
            return None
        if not ok:
            return None
        pressure = max(0.0, min(1.0, float(value)))

        if self._is_stylus_event(event):
            return pressure

        tool = event.get_device_tool()
        if tool is not None:
            # Explicit mouse/lens tool — ignore any stub pressure axis.
            return None

        device = event.get_source_device() or event.get_device()
        if device is not None:
            axes = device.get_axes()
            if axes & Gdk.AxisFlags.PRESSURE:
                return pressure
            # Master pointer on Wayland: still accept a live pressure reading.
            if device.get_source() == Gdk.InputSource.MOUSE and 0.0 < pressure <= 1.0:
                return pressure

        return None

    def _begin_size_gesture(self, x: float, y: float) -> None:
        """Start button-3 size adjust: freeze the preview at the press point.

        Width stays put until the tip moves past the click threshold (drag) or
        the button is released without dragging (cycle snap-mark presets).
        """
        self._toolbar._ensure_draw_mode()
        self._size_anchor = (x, y)
        self._size_pointer = (x, y)
        self._size_dragged = False
        # Keep the logical cursor on the anchor so the ring stays put.
        self._set_cursor_pos(x, y)
        self._refresh_pointer_cursor()
        self._invalidate_cursor(self._size_anchor)
        self._invalidate_size_pointer(self._size_pointer)

    def _end_size_gesture(self, *, commit: bool = False) -> None:
        if self._size_anchor is None:
            return
        anchor = self._size_anchor
        tip = self._size_pointer
        was_drag = self._size_dragged
        self._size_anchor = None
        self._size_pointer = None
        self._size_dragged = False
        if commit and not was_drag:
            # Click (no drag): cycle pen snap marks, or jump eraser to max.
            self._toolbar.cycle_size_preset()
        self._refresh_pointer_cursor()
        self._invalidate_cursor(anchor, self._cursor_pos)
        self._invalidate_size_pointer(tip)

    def _update_size_gesture(self, x: float, y: float) -> bool:
        """Map tip distance from the frozen press point onto stroke width."""
        if self._size_anchor is None:
            return False
        ax, ay = self._size_anchor
        dist = math.hypot(x - ax, y - ay)
        old_tip = self._size_pointer
        self._size_pointer = (x, y)

        if not self._size_dragged:
            if dist < _SIZE_GESTURE_CLICK_PX:
                # Still a potential click — move the tip marker only.
                self._invalidate_size_pointer(old_tip, self._size_pointer)
                return True
            self._size_dragged = True

        width_max = max_width_for_tool(self._ink.tool)
        width = max(
            WIDTH_MIN,
            min(width_max, _width_from_size_gesture_dist(dist, width_max)),
        )
        old_pad = self._cursor_pad()
        if abs(width - self._ink.width) >= 0.05:
            self._toolbar._set_width(width, emit=True)
        pad = max(old_pad, self._cursor_pad())
        self.queue_draw_area(
            int(ax - pad),
            int(ay - pad),
            int(pad * 2) + 2,
            int(pad * 2) + 2,
        )
        self._invalidate_size_pointer(old_tip, self._size_pointer)
        return True

    def _on_stylus_button_press(self, event: Gdk.EventButton, x: float, y: float) -> bool:
        """Barrel buttons: 2 toggles pen/eraser; 3 starts size adjust gesture."""
        if not self._is_stylus_event(event):
            return False
        if event.button == 2:
            nxt = Tool.ERASER if self._ink.tool is Tool.PEN else Tool.PEN
            self._toolbar._select_tool(nxt)
            return True
        if event.button == 3:
            self._begin_size_gesture(x, y)
            return True
        return False

    def _on_button_press(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        x, y = self._event_coords(event)
        self._set_cursor_pos(x, y)
        if self._on_stylus_button_press(event, x, y):
            return True
        if event.button != 1 or not self._ink.draw_enabled:
            return False
        if self._size_anchor is not None:
            return True
        if self._event_over_toolbar(x, y):
            return False
        self._ensure_ink_visible()
        if self._ink.begin_stroke(x, y, self._event_pressure(event)):
            self.queue_draw()
            return True
        return False

    def _on_button_release(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        # If the grip armed fullscreen input but Wayland delivered release here
        # instead of to the grip, clear the click-sink immediately.
        if event.button == 1 and self._toolbar.has_pointer_capture():
            self._toolbar.end_pointer_capture()
        x, y = self._event_coords(event)
        if event.button == 3 and self._size_anchor is not None:
            self._end_size_gesture(commit=True)
            self._set_cursor_pos(x, y)
            return True
        old = self._set_cursor_pos(x, y)
        if self._show_brush_size_cursor() or self._ink.tool is Tool.ERASER:
            self._invalidate_cursor(old, self._cursor_pos)
        if event.button != 1:
            return False
        if self._ink.end_stroke():
            self.queue_draw()
            return True
        return False

    def _on_motion(self, _w: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        x, y = self._event_coords(event)
        # During resize the preview stays pinned; only distance from the
        # press point matters — do not move _cursor_pos with the stylus.
        if self._size_anchor is not None:
            return self._update_size_gesture(x, y)
        old = self._set_cursor_pos(x, y)
        handled = False
        if event.state & Gdk.ModifierType.BUTTON1_MASK:
            if self._cursor_pos is not None and self._ink.continue_stroke(
                x, y, self._event_pressure(event)
            ):
                self.queue_draw()
                handled = True
        elif old != self._cursor_pos and (
            self._ink.tool is Tool.ERASER or self._show_brush_size_cursor()
        ):
            # Must invalidate when the ring disappears (e.g. over the toolbar),
            # not only while it is still shown — otherwise a ghost sticks.
            self._invalidate_cursor(old, self._cursor_pos)
        return handled

    def _on_enter(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        x, y = self._event_coords(event)
        if self._size_anchor is not None:
            return self._update_size_gesture(x, y)
        old = self._set_cursor_pos(x, y)
        if old != self._cursor_pos and (
            self._ink.tool is Tool.ERASER or self._show_brush_size_cursor()
        ):
            self._invalidate_cursor(old, self._cursor_pos)
        return False

    def _on_leave(self, _w: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self._end_size_gesture()
        old = self._cursor_pos
        self._cursor_pos = None
        self._invalidate_cursor(old)
        self._refresh_pointer_cursor()
        return False

    def _on_touch(self, _w: Gtk.Widget, event: Gdk.EventTouch) -> bool:
        if not self._ink.draw_enabled:
            return False
        x, y = self._event_coords(event)
        pressure = self._event_pressure(event)
        if event.type == Gdk.EventType.TOUCH_BEGIN:
            self._set_cursor_pos(x, y)
            if self._event_over_toolbar(x, y):
                return False
            self._ensure_ink_visible()
            if self._ink.begin_stroke(x, y, pressure):
                self.queue_draw()
                return True
        elif event.type == Gdk.EventType.TOUCH_UPDATE:
            old = self._set_cursor_pos(x, y)
            if self._cursor_pos is not None and self._ink.continue_stroke(
                x, y, pressure
            ):
                self.queue_draw()
                return True
            if old != self._cursor_pos and (
                self._ink.tool is Tool.ERASER or self._show_brush_size_cursor()
            ):
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
            if self._toolbar.has_pointer_capture():
                self._toolbar.end_pointer_capture()
                return True
            if self._toolbar.is_board_open():
                self._toolbar.set_board_open(False, emit=True)
            elif self._toolbar.is_draw_mode():
                self._toolbar.set_draw_mode(False, emit=True)
            elif self._tray is not None:
                self.hide_to_tray()
            else:
                self.quit_app()
            return True
        if key == "b" and not ctrl:
            self._toolbar.set_board_open(
                not self._toolbar.is_board_open(), emit=True
            )
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


def run(argv: list[str] | None = None) -> int:
    """Run Glassboard as a single-instance Gtk.Application.

    Pinning the .desktop entry and clicking it again activates this process
    (shows the overlay) instead of spawning another tray icon.
    """
    GLib.set_prgname("glassboard")
    GLib.set_application_name("Glassboard")

    app = Gtk.Application(
        application_id=APPLICATION_ID,
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )
    state: dict[str, OverlayWindow | None] = {"win": None}

    def on_activate(_application: Gtk.Application) -> None:
        win = state["win"]
        if win is None:
            win = OverlayWindow(application=app)
            state["win"] = win
            try:
                from glassboard.tray import attach_tray

                attach_tray(win)
            except Exception:
                # Tray is optional — overlay still runs without it.
                pass
            win.show_all()
            GLib.idle_add(win._ensure_toolbar_placed)
            return
        # Taskbar / launcher re-activation: restore if hidden to tray.
        win.show_from_tray()

    app.connect("activate", on_activate)
    return int(app.run(argv))
