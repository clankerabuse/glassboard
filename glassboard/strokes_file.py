"""Save / load stroke documents (.glassboard JSON) and recent-path memory."""

from __future__ import annotations

import json
import math
from pathlib import Path

import cairo

from glassboard.canvas import Stroke, Tool, brush_radius
from glassboard.config import (
    load_strokes_last_dir,
    load_strokes_recent,
    remember_strokes_path,
)

FILE_VERSION = 1
FILE_EXTENSION = ".glassboard"
_PREVIEW_PAD = 12.0
_RECENT_MAX = 4


class StrokesFileError(Exception):
    """Invalid or unreadable stroke document."""


def default_strokes_dir() -> Path:
    """Preferred starting folder when nothing has been saved yet."""
    docs = Path.home() / "Documents" / "Glassboard"
    return docs


def strokes_chooser_dir() -> Path:
    """Directory to open in the file chooser (last used, else default)."""
    last = load_strokes_last_dir()
    if last is not None and last.is_dir():
        return last
    preferred = default_strokes_dir()
    if preferred.is_dir():
        return preferred
    return Path.home()


def list_recent_strokes() -> list[Path]:
    """Existing recent .glassboard paths, newest first."""
    out: list[Path] = []
    for path in load_strokes_recent():
        if path.is_file() and path not in out:
            out.append(path)
        if len(out) >= _RECENT_MAX:
            break
    return out


def strokes_to_document(strokes: list[Stroke]) -> dict:
    return {
        "version": FILE_VERSION,
        "strokes": [_stroke_to_json(s) for s in strokes],
    }


def document_to_strokes(data: object) -> list[Stroke]:
    if not isinstance(data, dict):
        raise StrokesFileError("document root must be an object")
    version = data.get("version", 1)
    if version != FILE_VERSION:
        raise StrokesFileError(f"unsupported version: {version!r}")
    raw = data.get("strokes")
    if not isinstance(raw, list):
        raise StrokesFileError("missing strokes list")
    strokes: list[Stroke] = []
    for i, item in enumerate(raw):
        try:
            strokes.append(_stroke_from_json(item))
        except StrokesFileError as exc:
            raise StrokesFileError(f"stroke {i}: {exc}") from exc
    return strokes


def save_strokes(path: Path, strokes: list[Stroke]) -> Path:
    path = path.expanduser()
    if path.suffix.lower() != FILE_EXTENSION:
        path = path.with_suffix(FILE_EXTENSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(strokes_to_document(strokes), indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    remember_strokes_path(path)
    return path


def load_strokes(path: Path, *, remember: bool = True) -> list[Stroke]:
    path = path.expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StrokesFileError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise StrokesFileError(f"invalid JSON: {exc}") from exc
    strokes = document_to_strokes(raw)
    if remember:
        remember_strokes_path(path)
    return strokes


def render_strokes_preview(
    strokes: list[Stroke],
    *,
    width: int = 180,
    height: int = 110,
) -> cairo.ImageSurface:
    """Rasterize *strokes* fitted into a dark preview chip."""
    width = max(32, int(width))
    height = max(24, int(height))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)

    # Dark plate matching the toolbar.
    ctx.set_source_rgb(0.10, 0.10, 0.12)
    ctx.paint()

    bbox = _strokes_bbox(strokes)
    if bbox is None:
        return surface

    min_x, min_y, max_x, max_y = bbox
    span_x = max(1.0, max_x - min_x)
    span_y = max(1.0, max_y - min_y)
    avail_w = max(1.0, width - 2 * _PREVIEW_PAD)
    avail_h = max(1.0, height - 2 * _PREVIEW_PAD)
    scale = min(avail_w / span_x, avail_h / span_y)
    # Cap extreme zoom for a single tiny mark.
    scale = min(scale, 4.0)
    ox = (width - span_x * scale) * 0.5 - min_x * scale
    oy = (height - span_y * scale) * 0.5 - min_y * scale

    ctx.save()
    ctx.translate(ox, oy)
    ctx.scale(scale, scale)
    for stroke in strokes:
        _paint_preview_stroke(ctx, stroke, scale)
    ctx.restore()
    return surface


def _stroke_to_json(stroke: Stroke) -> dict:
    return {
        "tool": "eraser" if stroke.tool is Tool.ERASER else "pen",
        "color": [round(c, 5) for c in stroke.color],
        "width": round(float(stroke.width), 3),
        "points": [
            [round(x, 2), round(y, 2), round(w, 3)] for x, y, w in stroke.points
        ],
    }


def _stroke_from_json(item: object) -> Stroke:
    if not isinstance(item, dict):
        raise StrokesFileError("stroke must be an object")
    tool_raw = item.get("tool", "pen")
    if tool_raw == "eraser":
        tool = Tool.ERASER
    elif tool_raw == "pen":
        tool = Tool.PEN
    else:
        raise StrokesFileError(f"unknown tool {tool_raw!r}")
    color = item.get("color")
    if (
        not isinstance(color, (list, tuple))
        or len(color) != 4
        or any(not isinstance(c, (int, float)) for c in color)
    ):
        raise StrokesFileError("color must be [r,g,b,a]")
    try:
        width = float(item.get("width", 1.0))
    except (TypeError, ValueError) as exc:
        raise StrokesFileError("invalid width") from exc
    points_raw = item.get("points")
    if not isinstance(points_raw, list) or not points_raw:
        raise StrokesFileError("points must be a non-empty list")
    points: list[tuple[float, float, float]] = []
    for pt in points_raw:
        if not isinstance(pt, (list, tuple)) or len(pt) not in (2, 3):
            raise StrokesFileError("point must be [x,y] or [x,y,w]")
        try:
            x, y = float(pt[0]), float(pt[1])
            w = float(pt[2]) if len(pt) == 3 else width
        except (TypeError, ValueError) as exc:
            raise StrokesFileError("invalid point") from exc
        points.append((x, y, w))
    return Stroke(
        tool=tool,
        color=(float(color[0]), float(color[1]), float(color[2]), float(color[3])),
        width=width,
        points=points,
    )


def _strokes_bbox(
    strokes: list[Stroke],
) -> tuple[float, float, float, float] | None:
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    found = False
    for stroke in strokes:
        for x, y, w in stroke.points:
            r = brush_radius(w)
            min_x = min(min_x, x - r)
            min_y = min(min_y, y - r)
            max_x = max(max_x, x + r)
            max_y = max(max_y, y + r)
            found = True
    if not found:
        return None
    return (min_x, min_y, max_x, max_y)


def _paint_preview_stroke(
    ctx: cairo.Context, stroke: Stroke, scale: float
) -> None:
    """Simplified stroke paint for thumbnails (straight segments)."""
    if not stroke.points:
        return
    if stroke.tool is Tool.ERASER:
        # Punch through to the dark plate behind transparent holes.
        ctx.set_operator(cairo.OPERATOR_CLEAR)
    else:
        ctx.set_operator(cairo.OPERATOR_OVER)
        ctx.set_source_rgba(*stroke.color)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)

    pts = stroke.points
    if len(pts) == 1:
        x, y, w = pts[0]
        ctx.arc(x, y, brush_radius(w), 0, 2 * math.pi)
        ctx.fill()
        return

    # Line width is in user space; after scale the visual thickness is scale*w.
    # Keep a minimum ~1 device-pixel hairline so thin pens still show.
    min_user = (1.0 / max(scale, 1e-6)) if scale > 0 else 1.0
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        ctx.set_line_width(max(min_user, b[2]))
        ctx.move_to(a[0], a[1])
        ctx.line_to(b[0], b[1])
        ctx.stroke()
