"""Cairo ink surface: pen strokes, eraser, undo, and clear."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

import cairo

from glassboard import _gi  # noqa: F401


class Tool(Enum):
    PEN = auto()
    ERASER = auto()


INK_ALPHA = 0.95

COLORS: dict[str, tuple[float, float, float, float]] = {
    "white": (1.0, 1.0, 1.0, INK_ALPHA),
    "black": (0.05, 0.05, 0.05, INK_ALPHA),
    "red": (0.92, 0.22, 0.22, INK_ALPHA),
    "green": (0.18, 0.72, 0.32, INK_ALPHA),
    "blue": (0.22, 0.48, 0.95, INK_ALPHA),
    "yellow": (0.98, 0.82, 0.12, INK_ALPHA),
}

# Four toolbar slots (left → right). Customized colors persist via config.
DEFAULT_SWATCHES: tuple[tuple[float, float, float, float], ...] = (
    COLORS["red"],
    COLORS["blue"],
    COLORS["green"],
    COLORS["yellow"],
)

ColorRGBA = tuple[float, float, float, float]

WIDTH_MIN = 1.0
# Annotation-friendly pen ceiling; eraser can go wider for quick wipes.
WIDTH_PEN_MAX = 24.0
WIDTH_ERASER_MAX = 128.0
WIDTH_MAX = WIDTH_ERASER_MAX  # absolute ceiling across tools
WIDTH_DEFAULT = 6.0


def max_width_for_tool(tool: Tool) -> float:
    """Stroke-width upper bound for the active tool."""
    if tool is Tool.ERASER:
        return WIDTH_ERASER_MAX
    return WIDTH_PEN_MAX

# Stylus pressure → width. At full pressure the stroke matches the slider;
# light touch shrinks toward this fraction of the slider width.
_PRESSURE_MIN_FRAC = 0.18


def width_for_pressure(base_width: float, pressure: float | None) -> float:
    """Map tablet pressure (0..1) onto stroke width. None = mouse / no axis."""
    base = max(WIDTH_MIN, min(WIDTH_MAX, float(base_width)))
    if pressure is None:
        return base
    p = max(0.0, min(1.0, float(pressure)))
    # Ease-in so mid-pressure still feels substantial.
    t = p * p * (3.0 - 2.0 * p)
    lo = max(WIDTH_MIN, base * _PRESSURE_MIN_FRAC)
    return lo + (base - lo) * t

# Stroke feel: light EMA on input + midpoint quadratic curves when painting.
# Tuned for full-screen overlays — smooth without feeling laggy.
_SMOOTH_ALPHA = 0.55  # blend toward raw sample (higher = snappier)
_MIN_POINT_DIST2 = 1.5 ** 2  # skip micro-jitter after smoothing (px²)
# 1.0 = full midpoint curve (rounder); 0.0 = straight segments.
_CURVE_TENSION = 0.72

# Eraser auto-grow: only while a held stroke stays consistently fast.
_ERASER_GROW_DELAY_S = 0.40  # must be erasing this long before growth starts
_ERASER_GROW_MIN_SPEED = 320.0  # px/s — "reasonably fast"
_ERASER_GROW_CONSISTENCY_S = 0.18  # speed must stay up this long
_ERASER_GROW_RATE = 144.0  # width px gained per second of qualifying motion
_ERASER_GROW_MAX = 288.0  # ~4× prior ceiling; fine for big continuous sweeps
_ERASER_SPEED_WINDOW_S = 0.14  # recent samples used to estimate speed


def brush_radius(width: float) -> float:
    """Half the painted stroke thickness (matches Cairo line_width / round caps)."""
    return max(WIDTH_MIN / 2.0, float(width) / 2.0)


@dataclass
class Stroke:
    tool: Tool
    color: tuple[float, float, float, float]
    width: float
    # (x, y, width) — width may vary along eraser strokes that auto-grew.
    points: list[tuple[float, float, float]] = field(default_factory=list)


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

        # Eraser auto-grow state (valid only while an eraser stroke is active).
        self._erase_t0 = 0.0
        self._erase_width = WIDTH_DEFAULT
        self._erase_fast_since: float | None = None
        self._erase_samples: deque[tuple[float, float, float]] = deque()

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool

    def set_color(self, color: ColorRGBA | str) -> None:
        if isinstance(color, str):
            if color not in COLORS:
                return
            self.color = COLORS[color]
        else:
            r, g, b, a = color
            self.color = (float(r), float(g), float(b), float(a))
        self.tool = Tool.PEN

    def set_width(self, width: float) -> None:
        self.width = max(WIDTH_MIN, min(WIDTH_MAX, float(width)))

    def stroke_width(self) -> float:
        """Live brush width — includes eraser auto-grow during an active stroke."""
        if (
            self._active is not None
            and self._active.tool is Tool.ERASER
            and self._active.points
        ):
            return self._active.points[-1][2]
        return self.width

    def set_draw_enabled(self, enabled: bool) -> None:
        self.draw_enabled = enabled
        if not enabled:
            self._active = None
            self._reset_eraser_grow()

    def undo(self) -> None:
        if not self._strokes:
            return
        self._strokes.pop()
        self._replay()
        self._emit_changed()

    def clear(self) -> None:
        self._strokes.clear()
        self._active = None
        self._reset_eraser_grow()
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

    def begin_stroke(
        self, x: float, y: float, pressure: float | None = None
    ) -> bool:
        if not self.draw_enabled:
            return False
        width = width_for_pressure(self.width, pressure)
        self._active = Stroke(
            tool=self.tool,
            color=self.color,
            width=width,
            points=[(x, y, width)],
        )
        if self.tool is Tool.ERASER:
            self._start_eraser_grow(x, y, width)
        else:
            self._reset_eraser_grow()
        self._stroke_to_surface(self._active)
        return True

    def continue_stroke(
        self, x: float, y: float, pressure: float | None = None
    ) -> bool:
        if self._active is None:
            return False
        last = self._active.points[-1]
        # EMA toward the raw sample — damps hand/stylus jitter without a long lag.
        a = _SMOOTH_ALPHA
        x = last[0] + a * (x - last[0])
        y = last[1] + a * (y - last[1])
        if (x - last[0]) ** 2 + (y - last[1]) ** 2 < _MIN_POINT_DIST2:
            return True

        width = width_for_pressure(self.width, pressure)
        if self._active.tool is Tool.ERASER:
            # Auto-grow still expands the base; pressure scales the live tip.
            grown = self._update_eraser_grow(x, y)
            if pressure is None:
                width = grown
            else:
                width = width_for_pressure(grown, pressure)

        self._active.points.append((x, y, width))
        self._active.width = width
        self._stroke_to_surface(self._active, from_index=len(self._active.points) - 1)
        return True

    def end_stroke(self) -> bool:
        if self._active is None:
            return False
        # Curves stop at midpoints while drawing; close out to the real tip.
        self._stroke_to_surface(self._active, from_index=-1)
        self._strokes.append(self._active)
        self._active = None
        self._reset_eraser_grow()
        self._emit_changed()
        return True

    def _reset_eraser_grow(self) -> None:
        self._erase_t0 = 0.0
        self._erase_width = self.width
        self._erase_fast_since = None
        self._erase_samples.clear()

    def _start_eraser_grow(self, x: float, y: float, width: float) -> None:
        now = time.monotonic()
        self._erase_t0 = now
        self._erase_width = width
        self._erase_fast_since = None
        self._erase_samples.clear()
        self._erase_samples.append((now, x, y))

    def _update_eraser_grow(self, x: float, y: float) -> float:
        """Grow eraser width only while motion stays consistently fast."""
        now = time.monotonic()
        self._erase_samples.append((now, x, y))
        cutoff = now - _ERASER_SPEED_WINDOW_S
        while len(self._erase_samples) > 1 and self._erase_samples[0][0] < cutoff:
            self._erase_samples.popleft()

        speed = self._eraser_recent_speed()
        if speed >= _ERASER_GROW_MIN_SPEED:
            if self._erase_fast_since is None:
                self._erase_fast_since = now
        else:
            # Pause or slow / jittery motion — freeze size, don't grow.
            self._erase_fast_since = None

        held_long_enough = (now - self._erase_t0) >= _ERASER_GROW_DELAY_S
        consistent = (
            self._erase_fast_since is not None
            and (now - self._erase_fast_since) >= _ERASER_GROW_CONSISTENCY_S
        )
        if held_long_enough and consistent and len(self._erase_samples) >= 2:
            dt = now - self._erase_samples[-2][0]
            if dt > 0.0:
                self._erase_width = min(
                    _ERASER_GROW_MAX,
                    self._erase_width + _ERASER_GROW_RATE * dt,
                )

        return self._erase_width

    def _eraser_recent_speed(self) -> float:
        if len(self._erase_samples) < 2:
            return 0.0
        t0, x0, y0 = self._erase_samples[0]
        t1, x1, y1 = self._erase_samples[-1]
        dt = t1 - t0
        if dt <= 1e-4:
            return 0.0
        dist = math.hypot(x1 - x0, y1 - y0)
        # Path length across samples is a better "consistent motion" signal
        # than endpoint distance alone (less fooled by a single jump).
        path = 0.0
        prev = self._erase_samples[0]
        for sample in list(self._erase_samples)[1:]:
            path += math.hypot(sample[1] - prev[1], sample[2] - prev[2])
            prev = sample
        # Blend: require real travel, not just jitter in place.
        return max(dist, path * 0.85) / dt

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
        self._configure_stroke_ctx(ctx, stroke)

        pts = stroke.points
        x0, y0, w0 = pts[0]
        if len(pts) == 1:
            ctx.arc(x0, y0, brush_radius(w0), 0, 2 * math.pi)
            ctx.fill()
            return

        if len(pts) == 2:
            _stroke_line(ctx, pts[0], pts[1])
            return

        # Midpoint quadratic path: sharp corners at samples become soft curves.
        mid0 = _midpoint(pts[0], pts[1])
        _stroke_line(ctx, pts[0], (*mid0, pts[1][2]))
        for i in range(1, len(pts) - 1):
            mid_a = _midpoint(pts[i - 1], pts[i])
            mid_b = _midpoint(pts[i], pts[i + 1])
            _stroke_quad(ctx, mid_a, pts[i], mid_b, pts[i][2])
        mid_last = _midpoint(pts[-2], pts[-1])
        _stroke_line(ctx, (*mid_last, pts[-1][2]), pts[-1])

    def _stroke_to_surface(self, stroke: Stroke, from_index: int = 0) -> None:
        if self._surface is None or len(stroke.points) < 1:
            return
        ctx = cairo.Context(self._surface)
        if from_index == 0:
            self._paint_stroke(ctx, stroke)
            return

        self._configure_stroke_ctx(ctx, stroke)
        pts = stroke.points

        # Finish: last midpoint → tip (live ink only advances through midpoints).
        if from_index == -1:
            if len(pts) < 2:
                return
            mid = _midpoint(pts[-2], pts[-1])
            _stroke_line(ctx, (*mid, pts[-1][2]), pts[-1])
            return

        if from_index == 1 and len(pts) >= 2:
            mid = _midpoint(pts[0], pts[1])
            _stroke_line(ctx, pts[0], (*mid, pts[1][2]))
            return

        if from_index >= 2 and len(pts) >= from_index + 1:
            i = from_index - 1
            mid_a = _midpoint(pts[i - 1], pts[i])
            mid_b = _midpoint(pts[i], pts[i + 1])
            _stroke_quad(ctx, mid_a, pts[i], mid_b, pts[i][2])

    def _configure_stroke_ctx(self, ctx: cairo.Context, stroke: Stroke) -> None:
        if stroke.tool is Tool.ERASER:
            ctx.set_operator(cairo.OPERATOR_CLEAR)
        else:
            ctx.set_operator(cairo.OPERATOR_OVER)
            ctx.set_source_rgba(*stroke.color)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)

    def _emit_changed(self) -> None:
        if self.on_changed:
            self.on_changed()


def _midpoint(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float]:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _stroke_line(
    ctx: cairo.Context,
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> None:
    ctx.set_line_width(b[2])
    ctx.move_to(a[0], a[1])
    ctx.line_to(b[0], b[1])
    ctx.stroke()


def _stroke_quad(
    ctx: cairo.Context,
    start: tuple[float, float],
    control: tuple[float, float, float],
    end: tuple[float, float],
    width: float,
) -> None:
    # Pull the control point toward the chord so corners stay intentional
    # instead of ballooning into overly round arcs.
    t = _CURVE_TENSION
    chord_x = (start[0] + end[0]) * 0.5
    chord_y = (start[1] + end[1]) * 0.5
    cx = chord_x + t * (control[0] - chord_x)
    cy = chord_y + t * (control[1] - chord_y)
    ctx.set_line_width(width)
    ctx.move_to(start[0], start[1])
    ctx.curve_to(
        # Approximate a quadratic Bezier with a cubic for Cairo.
        start[0] + (2.0 / 3.0) * (cx - start[0]),
        start[1] + (2.0 / 3.0) * (cy - start[1]),
        end[0] + (2.0 / 3.0) * (cx - end[0]),
        end[1] + (2.0 / 3.0) * (cy - end[1]),
        end[0],
        end[1],
    )
    ctx.stroke()
