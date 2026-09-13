"""Floating toolbar: Click|Draw, colors, tools, width slider, undo/clear/quit."""

from __future__ import annotations

import math
from typing import Callable

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import Gdk, GLib, Gtk

from glassboard.board import BOARD_COLOR_DEFAULT
from glassboard.canvas import (
    COLORS,
    WIDTH_DEFAULT,
    WIDTH_MAX,
    WIDTH_MIN,
    Tool,
    brush_radius,
)

# Pixels of pointer travel before a grip press counts as a drag (not a click).
_DRAG_THRESHOLD_PX = 6
_PREVIEW_SIZE = 36  # Fixed chip; WIDTH_MAX is capped to fit 1:1 inside it.
_BOARD_MENU_SHOW_MS = 180
_BOARD_MENU_HIDE_MS = 280


class _SizePreview(Gtk.DrawingArea):
    """Fixed-size chip showing the brush/eraser at true pixel diameter."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(_PREVIEW_SIZE, _PREVIEW_SIZE)
        self._diameter = WIDTH_DEFAULT
        self._eraser = False
        self._color = COLORS["red"]
        self.connect("draw", self._on_draw)

    def set_preview(
        self,
        diameter: float,
        *,
        eraser: bool,
        color: tuple[float, float, float, float] | None = None,
    ) -> None:
        self._diameter = max(1.0, min(float(diameter), WIDTH_MAX))
        self._eraser = eraser
        if color is not None:
            self._color = color
        self.queue_draw()

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        alloc = self.get_allocation()
        cx = alloc.width / 2.0
        cy = alloc.height / 2.0
        radius = brush_radius(self._diameter)

        # Soft plate behind the preview.
        plate = min(alloc.width, alloc.height) / 2.0 - 1.0
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.arc(cx, cy, plate, 0, 2 * math.pi)
        cr.fill()

        if self._eraser:
            cr.set_source_rgba(1, 1, 1, 0.10)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.fill()
            cr.set_line_width(1.0)
            cr.set_source_rgba(0.05, 0.05, 0.08, 0.7)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(0.95, 0.95, 0.98, 0.95)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()
        else:
            cr.set_source_rgba(*self._color)
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.fill()
        return False


_BUTTON_ICON_PX = 22
_ICON_CLICK = "input-mouse-symbolic"
_ICON_BOARD = "video-display-symbolic"
_ICON_PEN = "document-edit-symbolic"


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


def _theme_icon_image(icon_name: str) -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
    image.set_pixel_size(_BUTTON_ICON_PX)
    return image


def _cairo_icon_image(draw_fn) -> Gtk.Image:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, _BUTTON_ICON_PX, _BUTTON_ICON_PX)
    cr = cairo.Context(surface)
    draw_fn(cr, _BUTTON_ICON_PX)
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, _BUTTON_ICON_PX, _BUTTON_ICON_PX)
    return Gtk.Image.new_from_pixbuf(pixbuf)


def _icon_button(icon_name: str, tooltip: str, css_class: str) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_image(_theme_icon_image(icon_name))
    btn.set_always_show_image(True)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_tooltip_text(tooltip)
    btn.get_style_context().add_class("icon-button")
    btn.get_style_context().add_class(css_class)
    return btn


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
        on_color: Callable[[str], None],
        on_width: Callable[[float], None],
        on_undo: Callable[[], None],
        on_clear: Callable[[], None],
        on_quit: Callable[[], None],
        on_board: Callable[[bool, str], None] | None = None,
        on_board_menu: Callable[[bool], None] | None = None,
        on_moved: Callable[[], None] | None = None,
        on_drag_begin: Callable[[], None] | None = None,
        on_drag_end: Callable[[], None] | None = None,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.set_visible_window(True)
        self.get_style_context().add_class("glassboard-toolbar")

        self._on_mode = on_mode
        self._on_tool = on_tool
        self._on_color = on_color
        self._on_width = on_width
        self._on_board = on_board
        self._on_board_menu = on_board_menu
        self._on_moved = on_moved
        self._on_drag_begin = on_drag_begin
        self._on_drag_end = on_drag_end
        self._on_layout_changed = on_layout_changed
        self._draw_mode = False
        self._board_open = False
        self._board_color = BOARD_COLOR_DEFAULT
        self._board_menu_show_id = 0
        self._board_menu_hide_id = 0
        self._tool = Tool.PEN
        self._width = WIDTH_DEFAULT
        self._color_name = "red"
        self._press_root: tuple[float, float] | None = None
        self._press_pos: tuple[int, int] | None = None
        self._dragging = False
        self._pos = (0, 0)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .glassboard-toolbar {
                background-color: rgba(18, 18, 22, 0.88);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.12);
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
            .glassboard-toolbar .board-menu {
                background-color: rgba(18, 18, 22, 0.94);
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 0.12);
                padding: 6px;
            }
            .glassboard-toolbar .board-chip {
                min-width: 72px;
                min-height: 28px;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.18);
                padding: 4px 10px;
            }
            .glassboard-toolbar .board-chip.white {
                background-color: rgb(245, 245, 240);
                color: #1a1a1e;
            }
            .glassboard-toolbar .board-chip.black {
                background-color: rgb(20, 20, 24);
                color: #f2f2f4;
            }
            .glassboard-toolbar .board-chip.active {
                border-color: rgba(70, 140, 255, 0.95);
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
                color: rgba(255, 255, 255, 0.55);
                padding: 4px 6px;
                letter-spacing: 1px;
            }
            .glassboard-toolbar .grip:hover {
                color: rgba(255, 255, 255, 0.85);
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

        # Drag handle (⠿): drag to reposition the toolbar.
        self._grip = Gtk.EventBox()
        self._grip.set_visible_window(False)
        self._grip.set_tooltip_text("Drag to move")
        grip_label = Gtk.Label(label="⠿")
        grip_label.get_style_context().add_class("grip")
        self._grip.add(grip_label)
        self._grip.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self._grip.connect("button-press-event", self._on_grip_press)
        self._grip.connect("button-release-event", self._on_grip_release)
        self._grip.connect("motion-notify-event", self._on_grip_motion)
        root.pack_start(self._grip, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Mode toggle
        self._btn_click = _icon_button(_ICON_CLICK, "Click-through mode", "mode-click")
        self._btn_click.connect("clicked", lambda *_: self.set_draw_mode(False, emit=True))
        root.pack_start(self._btn_click, False, False, 0)

        self._btn_board = _icon_button(
            _ICON_BOARD,
            "Toggle board · hover for White / Black",
            "mode-board",
        )
        self._btn_board.connect("clicked", self._on_board_clicked)
        self._btn_board.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._btn_board.connect("enter-notify-event", self._on_board_btn_enter)
        self._btn_board.connect("leave-notify-event", self._on_board_btn_leave)
        root.pack_start(self._btn_board, False, False, 0)
        self._build_board_menu()

        root.pack_start(self._sep(), False, False, 0)

        # Tools
        self._btn_pen = _icon_button(_ICON_PEN, "Pen", "mode-pen")
        self._btn_eraser = _cairo_icon_button("Eraser", "mode-eraser", _draw_eraser_icon)
        self._btn_pen.connect("clicked", lambda *_: self._select_tool(Tool.PEN))
        self._btn_eraser.connect("clicked", lambda *_: self._select_tool(Tool.ERASER))
        root.pack_start(self._btn_pen, False, False, 0)
        root.pack_start(self._btn_eraser, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Colors
        self._swatches: dict[str, Gtk.Button] = {}
        for name, rgba in COLORS.items():
            btn = Gtk.Button()
            btn.get_style_context().add_class("swatch")
            r, g, b, _a = rgba
            provider = Gtk.CssProvider()
            provider.load_from_data(
                f"""
                button.swatch.{name} {{
                    background-color: rgb({int(r*255)}, {int(g*255)}, {int(b*255)});
                    background-image: none;
                }}
                """.encode()
            )
            btn.get_style_context().add_class(name)
            btn.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            btn.set_tooltip_text(name.capitalize())
            btn.connect("clicked", lambda _b, n=name: self._select_color(n))
            self._swatches[name] = btn
            root.pack_start(btn, False, False, 0)

        root.pack_start(self._sep(), False, False, 0)

        # Size slider + brush/eraser preview
        self._size_preview = _SizePreview()
        self._size_preview.set_tooltip_text("Brush size")
        root.pack_start(self._size_preview, False, False, 0)

        adjustment = Gtk.Adjustment(
            value=WIDTH_DEFAULT,
            lower=WIDTH_MIN,
            upper=WIDTH_MAX,
            step_increment=1.0,
            page_increment=4.0,
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
        self._select_color("red", emit=False)
        self._set_width(WIDTH_DEFAULT, emit=False)
        self.set_draw_mode(False, emit=False)
        self.set_board_open(False, emit=False)

        self.show_all()
        self._update_size_preview()

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
        if color is not None:
            self._board_color = color
        self._board_open = open_
        self._set_active(self._btn_board, open_)
        self._sync_board_chip_active()
        self._btn_board.set_tooltip_text(
            "Hide board · hover for White / Black"
            if open_
            else "Toggle board · hover for White / Black"
        )
        if emit and self._on_board is not None:
            self._on_board(open_, self._board_color)

    def _on_board_clicked(self, *_args) -> None:
        # Plain click toggles using the last-used board color.
        self.set_board_open(not self._board_open, emit=True)

    def _build_board_menu(self) -> None:
        self._board_popover = Gtk.Popover.new(self._btn_board)
        self._board_popover.set_position(Gtk.PositionType.TOP)
        self._board_popover.set_modal(False)
        self._board_popover.get_style_context().add_class("glassboard-toolbar")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.get_style_context().add_class("board-menu")
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        self._board_chips: dict[str, Gtk.Button] = {}
        for name, label in (("white", "White"), ("black", "Black")):
            btn = Gtk.Button(label=label)
            ctx = btn.get_style_context()
            ctx.add_class("board-chip")
            ctx.add_class(name)
            btn.connect("clicked", lambda _b, n=name: self._pick_board_color(n))
            self._board_chips[name] = btn
            box.pack_start(btn, False, False, 0)

        # Keep the popover open while the pointer is over it.
        event_box = Gtk.EventBox()
        event_box.add(box)
        event_box.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        event_box.connect("enter-notify-event", self._on_board_menu_enter)
        event_box.connect("leave-notify-event", self._on_board_menu_leave)

        self._board_popover.add(event_box)
        event_box.show_all()
        self._sync_board_chip_active()

    def _sync_board_chip_active(self) -> None:
        for name, btn in self._board_chips.items():
            self._set_active(btn, name == self._board_color)

    def _pick_board_color(self, color: str) -> None:
        self._cancel_board_menu_timers()
        self._board_popover.popdown()
        self._notify_board_menu(False)
        # Choosing a color always opens (or recolors) the board.
        self.set_board_open(True, color=color, emit=True)

    def _notify_board_menu(self, open_: bool) -> None:
        if self._on_board_menu is not None:
            self._on_board_menu(open_)

    def _cancel_board_menu_timers(self) -> None:
        if self._board_menu_show_id:
            GLib.source_remove(self._board_menu_show_id)
            self._board_menu_show_id = 0
        if self._board_menu_hide_id:
            GLib.source_remove(self._board_menu_hide_id)
            self._board_menu_hide_id = 0

    def _on_board_btn_enter(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_board_menu_timers()

        def _show() -> bool:
            self._board_menu_show_id = 0
            self._notify_board_menu(True)
            self._sync_board_chip_active()
            self._board_popover.popup()
            return False

        self._board_menu_show_id = GLib.timeout_add(_BOARD_MENU_SHOW_MS, _show)
        return False

    def _on_board_btn_leave(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._schedule_board_menu_hide()
        return False

    def _on_board_menu_enter(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_board_menu_timers()
        return False

    def _on_board_menu_leave(self, _w: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._schedule_board_menu_hide()
        return False

    def _schedule_board_menu_hide(self) -> None:
        self._cancel_board_menu_timers()

        def _hide() -> bool:
            self._board_menu_hide_id = 0
            self._board_popover.popdown()
            self._notify_board_menu(False)
            return False

        self._board_menu_hide_id = GLib.timeout_add(_BOARD_MENU_HIDE_MS, _hide)

    def _select_tool(self, tool: Tool, *, emit: bool = True) -> None:
        self._tool = tool
        self._sync_mode_buttons()
        self._update_size_preview()
        if emit:
            self._on_tool(tool)
            self._ensure_draw_mode()
            if self._on_layout_changed:
                self._on_layout_changed()

    def _select_color(self, name: str, *, emit: bool = True) -> None:
        for n, btn in self._swatches.items():
            self._set_active(btn, n == name)
        self._color_name = name
        self._tool = Tool.PEN
        self._sync_mode_buttons()
        self._update_size_preview()
        if emit:
            self._on_tool(Tool.PEN)
            self._on_color(name)
            self._ensure_draw_mode()
            if self._on_layout_changed:
                self._on_layout_changed()

    def _set_width(self, width: float, *, emit: bool = True) -> None:
        self._width = max(WIDTH_MIN, min(WIDTH_MAX, width))
        if abs(self._size_scale.get_value() - self._width) > 0.01:
            self._size_scale.handler_block_by_func(self._on_size_changed)
            self._size_scale.set_value(self._width)
            self._size_scale.handler_unblock_by_func(self._on_size_changed)
        self._update_size_preview()
        if emit:
            self._on_width(self._width)

    def _on_size_changed(self, scale: Gtk.Scale) -> None:
        self._width = scale.get_value()
        self._update_size_preview()
        self._on_width(self._width)
        self._ensure_draw_mode()

    def _update_size_preview(self) -> None:
        eraser = self._tool is Tool.ERASER
        color = COLORS.get(self._color_name, COLORS["red"])
        self._size_preview.set_preview(self._width, eraser=eraser, color=color)
        self._size_preview.set_tooltip_text(
            "Eraser size" if eraser else "Pen size"
        )
        self._size_preview.show()

    # --- drag handle --------------------------------------------------------

    def get_pos(self) -> tuple[int, int]:
        return self._pos

    def set_pos(self, x: int, y: int) -> None:
        self._pos = (x, y)

    def _on_grip_press(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        self._press_root = (event.x_root, event.y_root)
        self._press_pos = self._pos
        self._dragging = False
        return True

    def _on_grip_release(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        was_dragging = self._dragging
        self._press_root = None
        self._press_pos = None
        self._dragging = False
        if was_dragging:
            if self._on_moved:
                self._on_moved()
            if self._on_drag_end:
                self._on_drag_end()
        return True

    def _on_grip_motion(self, _w: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._press_root is None or self._press_pos is None:
            return False
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False

        dx = event.x_root - self._press_root[0]
        dy = event.y_root - self._press_root[1]
        if not self._dragging:
            if dx * dx + dy * dy < _DRAG_THRESHOLD_PX * _DRAG_THRESHOLD_PX:
                return True
            self._dragging = True
            # Take fullscreen input for the duration of the drag so Wayland
            # keeps delivering motion once the pointer leaves the old hit box.
            if self._on_drag_begin:
                self._on_drag_begin()

        self._pos = (int(self._press_pos[0] + dx), int(self._press_pos[1] + dy))
        if self._on_moved:
            self._on_moved()
        return True
