"""Floating toolbar: Click|Draw, colors, tools, width slider, undo/clear/quit."""

from __future__ import annotations

import math
from typing import Callable

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import GLib, Gdk, Gtk

from glassboard.board import BOARD_COLOR_DEFAULT, BOARD_COLORS
from glassboard.canvas import (
    INK_ALPHA,
    WIDTH_DEFAULT,
    WIDTH_MIN,
    ColorRGBA,
    Tool,
    max_width_for_tool,
)
from glassboard.config import load_swatches, save_swatches

# Pixels of pointer travel before a grip press counts as a drag (not a click).
_DRAG_THRESHOLD_PX = 6
_BOARD_COLOR_ORDER = ("white", "black")


_BUTTON_ICON_PX = 22


def _draw_click_icon(cr: cairo.Context, size: int) -> None:
    """Mouse / click-through glyph (static pixbuf — not a symbolic theme icon)."""
    s = float(size)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(1.4, s * 0.08))
    cr.set_source_rgba(0.95, 0.95, 0.98, 0.95)

    x, y = s * 0.30, s * 0.12
    w, h = s * 0.40, s * 0.72
    r = s * 0.12
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.stroke()
    cr.move_to(x + w / 2, y + s * 0.04)
    cr.line_to(x + w / 2, y + s * 0.28)
    cr.stroke()
    cr.arc(x + w / 2, y + s * 0.22, s * 0.045, 0, 2 * math.pi)
    cr.fill()


def _draw_pen_icon(cr: cairo.Context, size: int) -> None:
    """Pen nib glyph (static pixbuf — not a symbolic theme icon)."""
    s = float(size)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(1.4, s * 0.08))
    cr.set_source_rgba(0.95, 0.95, 0.98, 0.95)

    cr.move_to(s * 0.22, s * 0.72)
    cr.line_to(s * 0.58, s * 0.20)
    cr.line_to(s * 0.72, s * 0.34)
    cr.line_to(s * 0.36, s * 0.86)
    cr.close_path()
    cr.stroke()
    cr.move_to(s * 0.22, s * 0.72)
    cr.line_to(s * 0.14, s * 0.86)
    cr.line_to(s * 0.36, s * 0.86)
    cr.stroke()


def _draw_eye_icon(cr: cairo.Context, size: int, *, open_: bool) -> None:
    """Eye glyph for the ink hide/show toggle (static pixbuf)."""
    s = float(size)
    cx, cy = s / 2.0, s / 2.0
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(1.4, s * 0.08))
    cr.set_source_rgba(0.95, 0.95, 0.98, 0.95)

    rx, ry = s * 0.38, s * 0.22
    cr.save()
    cr.translate(cx, cy)
    cr.scale(1.0, ry / rx)
    cr.arc(0.0, 0.0, rx, 0, 2 * math.pi)
    cr.restore()
    cr.stroke()

    if open_:
        cr.arc(cx, cy, s * 0.11, 0, 2 * math.pi)
        cr.fill()
    else:
        cr.move_to(cx - s * 0.28, cy + s * 0.28)
        cr.line_to(cx + s * 0.28, cy - s * 0.28)
        cr.stroke()


def _draw_eraser_icon(cr: cairo.Context, size: int) -> None:
    """Draw a large diagonal eraser icon filling the square."""
    s = size
    stroke_w = max(1.2, s * 0.06)
    cr.set_line_width(stroke_w)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    length = s * 0.78
    height = s * 0.44
    split = length * 0.42  # sleeve takes the left 42%

    cr.save()
    cr.translate(s / 2, s / 2)
    cr.rotate(-math.pi / 4)
    cr.rectangle(-length / 2, -height / 2, length, height)
    cr.clip()

    # Pink rubber (right portion).
    cr.set_source_rgba(0.95, 0.62, 0.68, 1.0)
    cr.rectangle(-length / 2, -height / 2, length, height)
    cr.fill()

    # Paper sleeve (left portion).
    cr.set_source_rgba(0.95, 0.95, 0.98, 0.9)
    cr.rectangle(-length / 2, -height / 2, split, height)
    cr.fill()

    # Outline + sleeve split line.
    cr.set_source_rgba(0.10, 0.10, 0.14, 1.0)
    cr.rectangle(-length / 2, -height / 2, length, height)
    cr.stroke()
    cr.move_to(-length / 2 + split, -height / 2)
    cr.line_to(-length / 2 + split, height / 2)
    cr.stroke()
    cr.restore()


def _draw_board_icon(cr: cairo.Context, size: int, color_name: str) -> None:
    """Filled board plate so the current White/Black choice is obvious."""
    s = float(size)
    pad = s * 0.14
    radius = s * 0.12
    x, y = pad, pad
    w = s - 2 * pad
    h = s - 2 * pad
    rgba = BOARD_COLORS.get(color_name, BOARD_COLORS[BOARD_COLOR_DEFAULT])

    # Rounded rect fill.
    cr.new_sub_path()
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(*rgba)
    cr.fill_preserve()
    # Bright outer ring so black (and the plate edge) read on the dark toolbar.
    cr.set_line_width(max(1.8, s * 0.09))
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
    cr.stroke_preserve()
    # Hairline inside the white ring for the white board plate.
    cr.set_line_width(max(1.0, s * 0.045))
    cr.set_source_rgba(0.10, 0.10, 0.14, 0.45)
    cr.stroke()


def _draw_grip_icon(cr: cairo.Context, width: int, height: int) -> None:
    """Two tall columns of dots — reads as a vertical drag handle."""
    cols = 2
    rows = 3
    # Slightly larger dots than the old braille glyph.
    radius = min(width, height) * 0.09
    gap_x = radius * 2.6
    gap_y = radius * 2.8
    total_w = (cols - 1) * gap_x
    total_h = (rows - 1) * gap_y
    ox = (width - total_w) / 2.0
    oy = (height - total_h) / 2.0
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.72)
    for row in range(rows):
        for col in range(cols):
            cr.arc(ox + col * gap_x, oy + row * gap_y, radius, 0, 2 * math.pi)
            cr.fill()


class _GripHandle(Gtk.EventBox):
    """Drag handle with a tall vertical-dot glyph and grab-hand cursor."""

    def __init__(self) -> None:
        super().__init__()
        self.set_visible_window(True)
        self.set_above_child(True)
        self.set_tooltip_text("Drag to move")
        self.get_style_context().add_class("grip")
        self.set_size_request(22, 36)

        self._canvas = Gtk.DrawingArea()
        self._canvas.set_size_request(22, 36)
        self._canvas.connect("draw", self._on_draw)
        self.add(self._canvas)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("realize", self._on_realize)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

        self._cursor_grab: Gdk.Cursor | None = None
        self._cursor_grabbing: Gdk.Cursor | None = None
        self._grabbing = False

    def _on_realize(self, *_args) -> None:
        display = self.get_display()
        self._cursor_grab = Gdk.Cursor.new_from_name(display, "grab")
        self._cursor_grabbing = Gdk.Cursor.new_from_name(display, "grabbing")
        self._apply_cursor()

    def set_grabbing(self, grabbing: bool) -> None:
        if self._grabbing == grabbing:
            return
        self._grabbing = grabbing
        self._apply_cursor()

    def _apply_cursor(self) -> None:
        window = self.get_window()
        if window is None:
            return
        cursor = self._cursor_grabbing if self._grabbing else self._cursor_grab
        if cursor is not None:
            window.set_cursor(cursor)

    def _on_enter(self, *_args) -> bool:
        if not self._grabbing:
            self._apply_cursor()
        return False

    def _on_leave(self, *_args) -> bool:
        return False

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        alloc = self._canvas.get_allocation()
        _draw_grip_icon(cr, alloc.width, alloc.height)
        return False


def _cairo_icon_pixbuf(draw_fn) -> Gdk.Pixbuf:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, _BUTTON_ICON_PX, _BUTTON_ICON_PX)
    cr = cairo.Context(surface)
    draw_fn(cr, _BUTTON_ICON_PX)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, _BUTTON_ICON_PX, _BUTTON_ICON_PX)


def _cairo_icon_image(draw_fn) -> Gtk.Image:
    return Gtk.Image.new_from_pixbuf(_cairo_icon_pixbuf(draw_fn))


def _cairo_icon_button(tooltip: str, css_class: str, draw_fn) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_image(_cairo_icon_image(draw_fn))
    btn.set_always_show_image(True)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_tooltip_text(tooltip)
    btn.get_style_context().add_class("icon-button")
    btn.get_style_context().add_class(css_class)
    return btn


class Toolbar(Gtk.EventBox):
    """Compact floating control cluster with a drag handle."""

    def __init__(
        self,
        *,
        on_mode: Callable[[bool], None],
        on_tool: Callable[[Tool], None],
        on_color: Callable[[ColorRGBA], None],
        on_width: Callable[[float], None],
        on_undo: Callable[[], None],
        on_clear: Callable[[], None],
        on_quit: Callable[[], None],
        on_board: Callable[[bool, str], None] | None = None,
        on_ink_visible: Callable[[bool], None] | None = None,
        on_moved: Callable[[], None] | None = None,
        on_drag_begin: Callable[[], None] | None = None,
        on_drag_end: Callable[[], None] | None = None,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.set_visible_window(True)
        self.set_app_paintable(False)
        self.get_style_context().add_class("glassboard-toolbar")

        self._on_mode = on_mode
        self._on_tool = on_tool
        self._on_color = on_color
        self._on_width = on_width
        self._on_board = on_board
        self._on_ink_visible = on_ink_visible
        self._on_moved = on_moved
        self._on_drag_begin = on_drag_begin
        self._on_drag_end = on_drag_end
        self._on_layout_changed = on_layout_changed
        self._draw_mode = False
        self._board_open = False
        self._board_color = BOARD_COLOR_DEFAULT
        self._ink_visible = True
        self._tool = Tool.PEN
        self._tool_widths: dict[Tool, float] = {
            Tool.PEN: WIDTH_DEFAULT,
            Tool.ERASER: WIDTH_DEFAULT,
        }
        self._slot_colors: list[ColorRGBA] = load_swatches()
        self._color_slot = 0
        self._swatch_providers: list[Gtk.CssProvider] = []
        self._color_popover: Gtk.Popover | None = None
        self._color_chooser: Gtk.ColorChooserWidget | None = None
        self._picker_slot: int | None = None
        self._press_root: tuple[float, float] | None = None
        self._press_pos: tuple[int, int] | None = None
        self._dragging = False
        # True while button1 is held on the grip (full input may be armed).
        self._drag_armed = False
        # Fail-safe poller that releases a capture whose button-release event
        # never reached us (would otherwise freeze every button in the app).
        self._capture_watch_id = 0
        self._pos = (0, 0)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .glassboard-toolbar {
                background-color: rgb(18, 18, 22);
                border-radius: 14px;
                /* Opaque border (precomputed 12% white over the bar color) so
                   ink behind the bar can never blend through the edge. */
                border: 1px solid rgb(46, 46, 50);
                padding: 6px;
            }
            .glassboard-toolbar button {
                background: transparent;
                border: none;
                border-radius: 8px;
                color: #f2f2f4;
                padding: 2px;
                min-width: 32px;
                min-height: 32px;
                font-size: 12px;
            }
            .glassboard-toolbar button.icon-button {
                min-width: 36px;
                min-height: 36px;
                padding: 0;
            }
            .glassboard-toolbar button,
            .glassboard-toolbar scale {
                outline-style: none;
                outline-width: 0;
            }
            .glassboard-toolbar button:hover {
                background-color: rgba(255, 255, 255, 0.10);
            }
            .glassboard-toolbar button.active {
                background-color: rgba(255, 255, 255, 0.18);
            }
            .glassboard-toolbar .mode-click.active,
            .glassboard-toolbar .mode-pen.active,
            .glassboard-toolbar .mode-eraser.active {
                background-color: rgba(70, 140, 255, 0.45);
            }
            .glassboard-toolbar .mode-board.active {
                background-color: rgba(240, 240, 235, 0.28);
            }
            .glassboard-toolbar .ink-visible.ink-hidden {
                background-color: rgba(255, 255, 255, 0.18);
            }
            .glassboard-toolbar .swatch {
                min-width: 22px;
                min-height: 22px;
                padding: 0;
                border-radius: 11px;
                border: 2px solid transparent;
            }
            .glassboard-toolbar .swatch.active {
                border-color: #ffffff;
            }
            .glassboard-toolbar .grip {
                min-width: 22px;
                min-height: 36px;
                padding: 4px 6px;
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            .glassboard-toolbar .grip:hover {
                background-color: rgba(255, 255, 255, 0.10);
            }
            .glassboard-toolbar scale {
                min-width: 110px;
                padding: 0 4px;
            }
            .glassboard-toolbar scale trough {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
                min-height: 4px;
            }
            .glassboard-toolbar scale highlight {
                background-color: rgba(70, 140, 255, 0.7);
                border-radius: 3px;
            }
            .glassboard-toolbar scale slider {
                background-color: #f2f2f4;
                border-radius: 8px;
                min-width: 14px;
                min-height: 14px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        root.set_margin_start(4)
        root.set_margin_end(4)
        root.set_margin_top(2)
        root.set_margin_bottom(2)
        self.add(root)

        # Drag handle: tall vertical dots; grab-hand cursor while hovering.
        self._grip = _GripHandle()
        self._grip.connect("button-press-event", self._on_grip_press)
        self._grip.connect("button-release-event", self._on_grip_release)
        self._grip.connect("motion-notify-event", self._on_grip_motion)
        root.pack_start(self._grip, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Mode toggle
        self._btn_click = _cairo_icon_button(
            "Click-through mode", "mode-click", _draw_click_icon
        )
        self._btn_click.connect("clicked", lambda *_: self.set_draw_mode(False, emit=True))
        root.pack_start(self._btn_click, False, False, 0)

        self._btn_board = _cairo_icon_button(
            "Toggle board · right-click cycles White/Black",
            "mode-board",
            lambda cr, size: _draw_board_icon(cr, size, self._board_color),
        )
        self._btn_board.connect("clicked", self._on_board_clicked)
        self._btn_board.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._btn_board.connect("button-press-event", self._on_board_button_press)
        root.pack_start(self._btn_board, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Tools
        self._btn_pen = _cairo_icon_button("Pen", "mode-pen", _draw_pen_icon)
        self._btn_eraser = _cairo_icon_button("Eraser", "mode-eraser", _draw_eraser_icon)
        self._btn_pen.connect("clicked", lambda *_: self._select_tool(Tool.PEN))
        self._btn_eraser.connect("clicked", lambda *_: self._select_tool(Tool.ERASER))
        root.pack_start(self._btn_pen, False, False, 0)
        root.pack_start(self._btn_eraser, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Colors — left-click selects; right-click opens an RGB picker.
        self._swatches: list[Gtk.Button] = []
        for i, rgba in enumerate(self._slot_colors):
            btn = Gtk.Button()
            btn.get_style_context().add_class("swatch")
            btn.get_style_context().add_class(f"slot-{i}")
            provider = Gtk.CssProvider()
            btn.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._swatch_providers.append(provider)
            self._apply_swatch_style(i, rgba)
            btn.set_tooltip_text("Left-click: use · Right-click: pick color")
            btn.connect("clicked", lambda _b, slot=i: self._select_color(slot))
            btn.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            btn.connect(
                "button-press-event",
                lambda _b, event, slot=i: self._on_swatch_button_press(
                    slot, event
                ),
            )
            self._swatches.append(btn)
            root.pack_start(btn, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Size slider (cursor ring / stylus gesture show the true size).
        adjustment = Gtk.Adjustment(
            value=WIDTH_DEFAULT,
            lower=WIDTH_MIN,
            upper=max_width_for_tool(Tool.PEN),
            step_increment=1.0,
            page_increment=8.0,
            page_size=0.0,
        )
        self._size_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=adjustment,
        )
        self._size_scale.set_draw_value(False)
        self._size_scale.set_size_request(110, -1)
        self._size_scale.set_tooltip_text("Stroke size")
        self._size_scale.connect("value-changed", self._on_size_changed)
        root.pack_start(self._size_scale, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        self._btn_ink_visible = _cairo_icon_button(
            "Hide markings",
            "ink-visible",
            lambda cr, size: _draw_eye_icon(cr, size, open_=True),
        )
        self._btn_ink_visible.connect(
            "clicked", lambda *_: self.set_ink_visible(not self._ink_visible)
        )
        root.pack_start(self._btn_ink_visible, False, False, 0)

        undo_btn = Gtk.Button(label="Undo")
        undo_btn.connect("clicked", lambda *_: on_undo())
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda *_: on_clear())
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda *_: on_quit())
        root.pack_start(undo_btn, False, False, 0)
        root.pack_start(clear_btn, False, False, 0)
        root.pack_start(quit_btn, False, False, 0)

        self._select_tool(Tool.PEN, emit=False)
        self._select_color(0, emit=False)
        # Apply persisted slot color without forcing Draw mode.
        self._on_color(self._slot_colors[0])
        self._set_width(WIDTH_DEFAULT, emit=False)
        self.set_draw_mode(False, emit=False)
        self.set_board_open(False, emit=False)
        self.set_ink_visible(True, emit=False)

        # No keyboard navigation in the bar — without this, every click moves
        # GTK's dotted focus ring between buttons, which reads as "icons
        # changing" whenever a toggle (e.g. hide/show) is pressed.
        self._disable_focus_rings()

        # The overlay is a layer-shell surface that rarely holds keyboard
        # focus, so GTK flips the bar into :backdrop (inactive-window) state
        # and the theme dims every button/label/swatch. Redraws triggered by
        # e.g. the hide/show toggle then repaint the bar in that dimmed style.
        # Strip BACKDROP from the whole subtree so the bar always renders in
        # its normal style.
        self._strip_backdrop()

        self.show_all()

    def _disable_focus_rings(self) -> None:
        def walk(widget: Gtk.Widget) -> None:
            widget.set_can_focus(False)
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)

        walk(self)

    def _strip_backdrop(self) -> None:
        def on_state_changed(widget: Gtk.Widget, _old: Gtk.StateFlags) -> None:
            if widget.get_state_flags() & Gtk.StateFlags.BACKDROP:
                # Re-entrant callback sees no BACKDROP flag, so this cannot loop.
                widget.unset_state_flags(Gtk.StateFlags.BACKDROP)

        def walk(widget: Gtk.Widget) -> None:
            widget.connect("state-flags-changed", on_state_changed)
            if widget.get_state_flags() & Gtk.StateFlags.BACKDROP:
                widget.unset_state_flags(Gtk.StateFlags.BACKDROP)
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    walk(child)

        walk(self)

    @staticmethod
    def _sep() -> Gtk.Separator:
        return Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)

    def _set_active(self, button: Gtk.Button, active: bool) -> None:
        ctx = button.get_style_context()
        if active:
            ctx.add_class("active")
        else:
            ctx.remove_class("active")

    def _sync_mode_buttons(self) -> None:
        """Ensure exactly one of click/pen/eraser is highlighted."""
        self._set_active(self._btn_click, not self._draw_mode)
        self._set_active(self._btn_pen, self._draw_mode and self._tool is Tool.PEN)
        self._set_active(self._btn_eraser, self._draw_mode and self._tool is Tool.ERASER)

    def _ensure_draw_mode(self) -> None:
        if not self._draw_mode:
            self.set_draw_mode(True, emit=True)

    def set_draw_mode(self, enabled: bool, *, emit: bool = True) -> None:
        self._draw_mode = enabled
        self._sync_mode_buttons()
        if emit:
            self._on_mode(enabled)

    def is_draw_mode(self) -> bool:
        return self._draw_mode

    def is_board_open(self) -> bool:
        return self._board_open

    def board_color(self) -> str:
        return self._board_color

    def set_board_open(
        self,
        open_: bool,
        *,
        color: str | None = None,
        emit: bool = True,
    ) -> None:
        if color is not None and color in BOARD_COLORS:
            self._board_color = color
        self._board_open = open_
        self._set_active(self._btn_board, open_)
        self._sync_board_icon()
        self._btn_board.set_tooltip_text(self._board_tooltip())
        if emit and self._on_board is not None:
            self._on_board(open_, self._board_color)

    def _board_tooltip(self) -> str:
        color = self._board_color.capitalize()
        if self._board_open:
            return f"Hide board ({color}) · right-click cycles White/Black"
        return f"Show board ({color}) · right-click cycles White/Black"

    def _sync_board_icon(self) -> None:
        self._btn_board.set_image(
            _cairo_icon_image(
                lambda cr, size: _draw_board_icon(cr, size, self._board_color)
            )
        )

    def _on_board_clicked(self, *_args) -> None:
        self.set_board_open(not self._board_open, emit=True)

    def _on_board_button_press(
        self, _w: Gtk.Widget, event: Gdk.EventButton
    ) -> bool:
        if event.button == 3:
            self.cycle_board_color()
            return True
        return False

    def cycle_board_color(self) -> None:
        """Flip White ↔ Black without forcing the board open."""
        try:
            idx = _BOARD_COLOR_ORDER.index(self._board_color)
        except ValueError:
            idx = 0
        next_color = _BOARD_COLOR_ORDER[(idx + 1) % len(_BOARD_COLOR_ORDER)]
        self.set_board_open(self._board_open, color=next_color, emit=True)

    def _select_tool(self, tool: Tool, *, emit: bool = True) -> None:
        self._tool = tool
        self._sync_width_limits()
        width = min(self._tool_widths[tool], max_width_for_tool(tool))
        self._tool_widths[tool] = width
        self._sync_width_slider(width)
        self._sync_mode_buttons()
        if emit:
            self._on_tool(tool)
            self._on_width(width)
            self._ensure_draw_mode()
            if self._on_layout_changed:
                self._on_layout_changed()

    def _apply_swatch_style(self, slot: int, rgba: ColorRGBA) -> None:
        r, g, b, _a = rgba
        self._swatch_providers[slot].load_from_data(
            f"""
            button.swatch.slot-{slot} {{
                background-color: rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)});
                background-image: none;
            }}
            """.encode()
        )

    def _on_swatch_button_press(self, slot: int, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            self._select_color(slot, emit=True)
            self._open_color_picker(slot)
            return True
        return False

    def _open_color_picker(self, slot: int) -> None:
        self._close_color_picker()
        self._picker_slot = slot

        popover = Gtk.Popover.new(self._swatches[slot])
        popover.set_position(Gtk.PositionType.TOP)
        chooser = Gtk.ColorChooserWidget()
        chooser.set_use_alpha(False)
        chooser.set_property("show-editor", False)
        rgba = Gdk.RGBA()
        r, g, b, _a = self._slot_colors[slot]
        rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b, 1.0
        chooser.set_rgba(rgba)
        chooser.connect("color-activated", self._on_picker_color_activated)
        chooser.connect("notify::rgba", self._on_picker_rgba_notify)
        popover.add(chooser)
        popover.connect("closed", self._on_color_picker_closed)
        chooser.show_all()
        self._color_popover = popover
        self._color_chooser = chooser
        popover.popup()

    def _close_color_picker(self) -> None:
        if self._color_popover is not None:
            self._color_popover.popdown()

    def _on_picker_color_activated(
        self, chooser: Gtk.ColorChooserWidget, *_args
    ) -> None:
        self._commit_picker_color(chooser.get_rgba(), persist=True)
        self._close_color_picker()

    def _on_picker_rgba_notify(self, chooser: Gtk.ColorChooserWidget, *_args) -> None:
        # Live-update the swatch while dragging the wheel.
        if self._picker_slot is None:
            return
        self._commit_picker_color(chooser.get_rgba(), persist=False)

    def _on_color_picker_closed(self, popover: Gtk.Popover, *_args) -> None:
        if self._color_chooser is not None:
            self._commit_picker_color(self._color_chooser.get_rgba(), persist=True)
        self._color_popover = None
        self._color_chooser = None
        self._picker_slot = None
        popover.destroy()

    def _commit_picker_color(
        self, gdk_rgba: Gdk.RGBA, *, persist: bool = True
    ) -> None:
        slot = self._picker_slot
        if slot is None:
            return
        color: ColorRGBA = (
            float(gdk_rgba.red),
            float(gdk_rgba.green),
            float(gdk_rgba.blue),
            INK_ALPHA,
        )
        if self._slot_colors[slot] == color and not persist:
            return
        self._slot_colors[slot] = color
        self._apply_swatch_style(slot, color)
        if slot == self._color_slot:
            self._on_color(color)
        if persist:
            try:
                save_swatches(self._slot_colors)
            except OSError:
                pass

    def _select_color(self, slot: int, *, emit: bool = True) -> None:
        if not 0 <= slot < len(self._swatches):
            slot = 0
        for i, btn in enumerate(self._swatches):
            self._set_active(btn, i == slot)
        self._color_slot = slot
        self._tool = Tool.PEN
        self._sync_width_limits()
        width = min(self._tool_widths[Tool.PEN], max_width_for_tool(Tool.PEN))
        self._tool_widths[Tool.PEN] = width
        self._sync_width_slider(width)
        self._sync_mode_buttons()
        if emit:
            self._on_tool(Tool.PEN)
            self._on_width(width)
            self._on_color(self._slot_colors[slot])
            self._ensure_draw_mode()
            if self._on_layout_changed:
                self._on_layout_changed()

    def _sync_width_limits(self) -> None:
        """Point the size slider at the active tool's max."""
        upper = max_width_for_tool(self._tool)
        adj = self._size_scale.get_adjustment()
        if abs(adj.get_upper() - upper) > 0.01:
            adj.set_upper(upper)

    def _sync_width_slider(self, width: float) -> None:
        if abs(self._size_scale.get_value() - width) > 0.01:
            self._size_scale.handler_block_by_func(self._on_size_changed)
            self._size_scale.set_value(width)
            self._size_scale.handler_unblock_by_func(self._on_size_changed)

    def _set_width(self, width: float, *, emit: bool = True) -> None:
        width = max(WIDTH_MIN, min(max_width_for_tool(self._tool), width))
        self._tool_widths[self._tool] = width
        self._sync_width_slider(width)
        if emit:
            self._on_width(width)

    def nudge_width(self, delta: float) -> None:
        """Adjust the active tool's stroke size by delta (e.g. mouse wheel)."""
        self._set_width(self._tool_widths[self._tool] + delta, emit=True)

    def _on_size_changed(self, scale: Gtk.Scale) -> None:
        width = max(WIDTH_MIN, min(max_width_for_tool(self._tool), scale.get_value()))
        self._tool_widths[self._tool] = width
        self._on_width(width)
        self._ensure_draw_mode()

    def is_ink_visible(self) -> bool:
        return self._ink_visible

    def set_ink_visible(self, visible: bool, *, emit: bool = True) -> None:
        """Show or hide drawn markings without clearing them."""
        self._ink_visible = bool(visible)
        self._sync_ink_visible_button()
        if emit and self._on_ink_visible is not None:
            self._on_ink_visible(self._ink_visible)

    def _sync_ink_visible_button(self) -> None:
        """Swap only the eye pixbuf in place — no widget rebuild, no style churn."""
        open_ = self._ink_visible
        pixbuf = _cairo_icon_pixbuf(
            lambda cr, size: _draw_eye_icon(cr, size, open_=open_)
        )
        image = self._btn_ink_visible.get_image()
        if isinstance(image, Gtk.Image):
            image.set_from_pixbuf(pixbuf)
        else:
            self._btn_ink_visible.set_image(Gtk.Image.new_from_pixbuf(pixbuf))
            self._btn_ink_visible.set_always_show_image(True)
        self._btn_ink_visible.set_tooltip_text(
            "Hide markings" if open_ else "Show markings"
        )
        # Background hint without touching the shared ".active" mode styles.
        ctx = self._btn_ink_visible.get_style_context()
        if open_:
            ctx.remove_class("ink-hidden")
        else:
            ctx.add_class("ink-hidden")

    # --- drag handle --------------------------------------------------------

    def get_pos(self) -> tuple[int, int]:
        return self._pos

    def set_pos(self, x: int, y: int) -> None:
        self._pos = (x, y)

    def has_pointer_capture(self) -> bool:
        """True while the grip is holding fullscreen input for a potential drag."""
        return self._drag_armed

    def end_pointer_capture(self) -> None:
        """End an armed/active grip drag (safe to call from the overlay window).

        Needed because once fullscreen input is enabled, Wayland may deliver
        button-release to the overlay instead of the grip — leaving the desktop
        stuck behind an invisible click sink.
        """
        if not self._drag_armed and not self._dragging:
            return
        was_dragging = self._dragging
        self._disarm_grip_pointer(was_dragging=was_dragging)

    def _arm_grip_pointer(self) -> None:
        if self._drag_armed:
            return
        self._drag_armed = True
        # Keep events on the grip even after the cursor leaves its widget window.
        # NOTE: must be the Gtk.Widget method — module-level Gtk.grab_add()
        # does not exist in PyGObject and raised AttributeError mid-handler,
        # which left the drag state half-armed (the random "frozen buttons").
        self._grip.grab_add()
        self._grip.set_grabbing(True)
        self._start_capture_watchdog()
        # Expand hit-testing immediately so motion outside the toolbar still
        # reaches us before the drag threshold (otherwise clicks fall through).
        if self._on_drag_begin:
            self._on_drag_begin()

    def _disarm_grip_pointer(self, *, was_dragging: bool) -> None:
        self._press_root = None
        self._press_pos = None
        self._dragging = False
        self._stop_capture_watchdog()
        if self._drag_armed:
            self._drag_armed = False
            # Unconditional: gtk_grab_remove is a safe no-op when not grabbed,
            # but skipping it when another widget sits on top of the grab
            # stack would leak our grab and swallow every future click.
            self._grip.grab_remove()
            self._grip.set_grabbing(False)
        if was_dragging and self._on_moved:
            self._on_moved()
        # Always restore the normal input region after arming full capture.
        if self._on_drag_end:
            self._on_drag_end()

    def _start_capture_watchdog(self) -> None:
        """While the grip holds capture, poll the real button state.

        The grip arms fullscreen input + a GTK grab on button press. If the
        matching release event is lost (Wayland can deliver it to another
        surface of ours, or not at all after a compositor hiccup), the grab
        would redirect every click in the app to the grip forever — the
        classic "all buttons frozen" state. GDK still tracks the device's
        modifier mask from whatever events it does see, so polling it lets us
        release the capture even when no event ever reaches the grip.
        """
        self._stop_capture_watchdog()
        self._capture_watch_id = GLib.timeout_add(200, self._capture_tick)

    def _stop_capture_watchdog(self) -> None:
        if self._capture_watch_id:
            GLib.source_remove(self._capture_watch_id)
            self._capture_watch_id = 0

    def _capture_tick(self) -> bool:
        if not self._drag_armed and not self._dragging:
            self._capture_watch_id = 0
            return False
        if self._grip_button_still_down():
            return True
        # Button is up but we never saw the release — free the capture now.
        self._capture_watch_id = 0
        self.end_pointer_capture()
        return False

    def _grip_button_still_down(self) -> bool:
        window = self.get_window()
        if window is None:
            return False
        seat = self.get_display().get_default_seat()
        pointer = seat.get_pointer() if seat is not None else None
        if pointer is None:
            return False
        _win, _x, _y, mask = window.get_device_position(pointer)
        return bool(mask & Gdk.ModifierType.BUTTON1_MASK)

    def _on_grip_press(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self._press_root = (event.x_root, event.y_root)
        self._press_pos = self._pos
        self._dragging = False
        self._arm_grip_pointer()
        return True

    def _on_grip_release(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        if not self._drag_armed and not self._dragging:
            return False
        was_dragging = self._dragging
        self._disarm_grip_pointer(was_dragging=was_dragging)
        return True

    def _on_grip_motion(self, _w: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._press_root is None or self._press_pos is None:
            return False
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            # Button lost without a release event (common on Wayland overlays).
            self.end_pointer_capture()
            return False

        dx = event.x_root - self._press_root[0]
        dy = event.y_root - self._press_root[1]
        if not self._dragging:
            if dx * dx + dy * dy < _DRAG_THRESHOLD_PX * _DRAG_THRESHOLD_PX:
                return True
            self._dragging = True

        self._pos = (int(self._press_pos[0] + dx), int(self._press_pos[1] + dy))
        if self._on_moved:
            self._on_moved()
        return True
