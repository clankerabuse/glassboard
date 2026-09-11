"""Cairo ink surface: pen strokes, eraser, undo, and clear."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import cairo

from glassboard import _gi  # noqa: F401


class Tool(Enum):
    PEN = auto()
    ERASER = auto()


COLORS: dict[str, tuple[float, float, float, float]] = {
    "white": (1.0, 1.0, 1.0, 0.95),
    "black": (0.05, 0.05, 0.05, 0.95),
    "red": (0.92, 0.22, 0.22, 0.95),
    "green": (0.18, 0.72, 0.32, 0.95),
    "blue": (0.22, 0.48, 0.95, 0.95),
    "yellow": (0.98, 0.82, 0.12, 0.95),
}

WIDTH_MIN = 2.0
# Matches the fixed toolbar preview chip (36px) with a small inset.
WIDTH_MAX = 32.0
WIDTH_DEFAULT = 6.0


def brush_radius(width: float) -> float:
    """Half the painted stroke thickness (matches Cairo line_width / round caps)."""
    return max(0.5, float(width) / 2.0)


@dataclass
class Stroke:
    tool: Tool
    color: tuple[float, float, float, float]
    width: float
    points: list[tuple[float, float]] = field(default_factory=list)


class InkBoard:
    """ARGB ink buffer painted by the overlay window — not a GTK widget."""

    def __init__(self) -> None:
        self.tool = Tool.PEN
        self.color = COLORS["red"]
        self.width = WIDTH_DEFAULT
        self.draw_enabled = False

        self._surface: cairo.ImageSurface | None = None
        self._strokes: list[Stroke] = []
        self._active: Stroke | None = None
        self._size = (1, 1)
        self.on_changed: Callable[[], None] | None = None

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool

    def set_color(self, name: str) -> None:
        if name in COLORS:
            self.color = COLORS[name]
            self.tool = Tool.PEN

    def set_width(self, width: float) -> None:
        self.width = max(WIDTH_MIN, min(WIDTH_MAX, float(width)))

    def stroke_width(self) -> float:
        """Width used for the active tool (pen or eraser)."""
        return self.width

    def set_draw_enabled(self, enabled: bool) -> None:
        self.draw_enabled = enabled
        if not enabled:
            self._active = None

    def undo(self) -> None:
        if not self._strokes:
            return
        self._strokes.pop()
        self._replay()
        self._emit_changed()

    def clear(self) -> None:
        self._strokes.clear()
        self._active = None
        self._clear_surface()
        self._emit_changed()

    def resize(self, width: int, height: int) -> None:
        self._ensure_surface(width, height)

    def paint(self, cr: cairo.Context) -> None:
        """Composite ink onto an already-cleared transparent window context."""
        if self._surface is None:
            return
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self._surface, 0, 0)
        cr.paint()

    def begin_stroke(self, x: float, y: float) -> bool:
        if not self.draw_enabled:
            return False
        self._active = Stroke(
            tool=self.tool,
            color=self.color,
            width=self.stroke_width(),
            points=[(x, y)],
        )
        self._stroke_to_surface(self._active)
        return True

    def continue_stroke(self, x: float, y: float) -> bool:
        if self._active is None:
            return False
        last = self._active.points[-1]
        if (x - last[0]) ** 2 + (y - last[1]) ** 2 < 0.5:
            return True
        self._active.points.append((x, y))
        self._stroke_to_surface(self._active, from_index=len(self._active.points) - 1)
        return True

    def end_stroke(self) -> bool:
        if self._active is None:
            return False
        self._strokes.append(self._active)
        self._active = None
        self._emit_changed()
        return True

    def _ensure_surface(self, width: int, height: int) -> None:
        width = max(1, width)
        height = max(1, height)
        if self._surface is not None and self._size == (width, height):
            return
        old = self._surface
        self._surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        self._size = (width, height)
        self._clear_surface()
        if old is not None:
            ctx = cairo.Context(self._surface)
            ctx.set_source_surface(old, 0, 0)
            ctx.paint()
        self._replay()

    def _clear_surface(self) -> None:
        if self._surface is None:
            return
        ctx = cairo.Context(self._surface)
        ctx.set_operator(cairo.OPERATOR_CLEAR)
        ctx.paint()

    def _replay(self) -> None:
        self._clear_surface()
        if self._surface is None:
            return
        ctx = cairo.Context(self._surface)
        for stroke in self._strokes:
            self._paint_stroke(ctx, stroke)

    def _paint_stroke(self, ctx: cairo.Context, stroke: Stroke) -> None:
        if len(stroke.points) < 1:
            return
        if stroke.tool is Tool.ERASER:
            ctx.set_operator(cairo.OPERATOR_CLEAR)
        else:
            ctx.set_operator(cairo.OPERATOR_OVER)
            ctx.set_source_rgba(*stroke.color)

        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_width(stroke.width)

        x0, y0 = stroke.points[0]
        if len(stroke.points) == 1:
            ctx.arc(x0, y0, brush_radius(stroke.width), 0, 2 * 3.14159265)
            ctx.fill()
            return

        ctx.move_to(x0, y0)
        for x, y in stroke.points[1:]:
            ctx.line_to(x, y)
        ctx.stroke()

    def _stroke_to_surface(self, stroke: Stroke, from_index: int = 0) -> None:
        if self._surface is None or len(stroke.points) < 1:
            return
        ctx = cairo.Context(self._surface)
        if from_index <= 0 or len(stroke.points) < 2:
            self._paint_stroke(ctx, stroke)
            return

        if stroke.tool is Tool.ERASER:
            ctx.set_operator(cairo.OPERATOR_CLEAR)
        else:
            ctx.set_operator(cairo.OPERATOR_OVER)
            ctx.set_source_rgba(*stroke.color)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_width(stroke.width)
        x0, y0 = stroke.points[from_index - 1]
        x1, y1 = stroke.points[from_index]
        ctx.move_to(x0, y0)
        ctx.line_to(x1, y1)
        ctx.stroke()

    def _emit_changed(self) -> None:
        if self.on_changed:
            self.on_changed()
