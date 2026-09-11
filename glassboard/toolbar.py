"""Floating toolbar: Click|Draw, colors, tools, width slider, undo/clear/quit."""

from __future__ import annotations

import math
from typing import Callable

import cairo

from glassboard import _gi  # noqa: F401
from gi.repository import Gdk, Gtk

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


class Toolbar(Gtk.EventBox):
    """Compact floating control cluster with a collapsible drag handle."""

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
        self._on_moved = on_moved
        self._on_drag_begin = on_drag_begin
        self._on_drag_end = on_drag_end
        self._on_layout_changed = on_layout_changed
        self._draw_mode = False
        self._collapsed = False
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
            .glassboard-toolbar.collapsed {
                padding: 4px;
                border-radius: 12px;
            }
            .glassboard-toolbar button {
                background: transparent;
                border: none;
                border-radius: 8px;
                color: #f2f2f4;
                padding: 4px 8px;
                min-height: 28px;
                font-size: 12px;
            }
            .glassboard-toolbar button:hover {
                background-color: rgba(255, 255, 255, 0.10);
            }
            .glassboard-toolbar button.active {
                background-color: rgba(255, 255, 255, 0.18);
            }
            .glassboard-toolbar .mode-draw.active {
                background-color: rgba(70, 140, 255, 0.45);
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

        # Drag handle (⠿): drag to move, click to collapse/expand.
        self._grip = Gtk.EventBox()
        self._grip.set_visible_window(False)
        self._grip.set_tooltip_text("Drag to move · Click to collapse")
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

        # Everything except the handle — hidden while retracted.
        self._controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        root.pack_start(self._controls, False, False, 0)

        self._controls.pack_start(self._sep(), False, False, 0)

        # Mode toggle
        self._btn_click = Gtk.Button(label="Click")
        self._btn_draw = Gtk.Button(label="Draw")
        self._btn_draw.get_style_context().add_class("mode-draw")
        self._btn_click.connect("clicked", lambda *_: self.set_draw_mode(False, emit=True))
        self._btn_draw.connect("clicked", lambda *_: self.set_draw_mode(True, emit=True))
        self._controls.pack_start(self._btn_click, False, False, 0)
        self._controls.pack_start(self._btn_draw, False, False, 0)

        self._controls.pack_start(self._sep(), False, False, 0)

        # Tools
        self._btn_pen = Gtk.Button(label="Pen")
        self._btn_eraser = Gtk.Button(label="Eraser")
        self._btn_pen.connect("clicked", lambda *_: self._select_tool(Tool.PEN))
        self._btn_eraser.connect("clicked", lambda *_: self._select_tool(Tool.ERASER))
        self._controls.pack_start(self._btn_pen, False, False, 0)
        self._controls.pack_start(self._btn_eraser, False, False, 0)

        self._controls.pack_start(self._sep(), False, False, 0)

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
            self._controls.pack_start(btn, False, False, 0)

        self._controls.pack_start(self._sep(), False, False, 0)

        # Size slider + brush/eraser preview
        self._size_preview = _SizePreview()
        self._size_preview.set_tooltip_text("Brush size")
        self._controls.pack_start(self._size_preview, False, False, 0)

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
        self._controls.pack_start(self._size_scale, False, False, 0)

        self._controls.pack_start(self._sep(), False, False, 0)

        undo_btn = Gtk.Button(label="Undo")
        undo_btn.connect("clicked", lambda *_: on_undo())
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", lambda *_: on_clear())
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda *_: on_quit())
        self._controls.pack_start(undo_btn, False, False, 0)
        self._controls.pack_start(clear_btn, False, False, 0)
        self._controls.pack_start(quit_btn, False, False, 0)

        self._select_tool(Tool.PEN, emit=False)
        self._select_color("red", emit=False)
        self._set_width(WIDTH_DEFAULT, emit=False)
        self.set_draw_mode(False, emit=False)

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

    def _ensure_draw_mode(self) -> None:
        if not self._draw_mode:
            self.set_draw_mode(True, emit=True)

    def set_draw_mode(self, enabled: bool, *, emit: bool = True) -> None:
        self._draw_mode = enabled
        self._set_active(self._btn_click, not enabled)
        self._set_active(self._btn_draw, enabled)
        if emit:
            self._on_mode(enabled)

    def is_draw_mode(self) -> bool:
        return self._draw_mode

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        if collapsed:
            self._controls.hide()
            self.get_style_context().add_class("collapsed")
            self._grip.set_tooltip_text("Drag to move · Click to expand")
        else:
            self._controls.show_all()
            self.get_style_context().remove_class("collapsed")
            self._grip.set_tooltip_text("Drag to move · Click to collapse")
            self._update_size_preview()
        if self._on_layout_changed:
            self._on_layout_changed()

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _select_tool(self, tool: Tool, *, emit: bool = True) -> None:
        self._tool = tool
        self._set_active(self._btn_pen, tool is Tool.PEN)
        self._set_active(self._btn_eraser, tool is Tool.ERASER)
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
        self._set_active(self._btn_pen, True)
        self._set_active(self._btn_eraser, False)
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
        else:
            # Plain click — retract/expand; keep Click/Draw mode as-is.
            self.toggle_collapsed()
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
