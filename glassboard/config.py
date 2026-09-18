"""Persistent user settings (swatch colors, tool widths, etc.)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from glassboard.canvas import (
    DEFAULT_SWATCHES,
    INK_ALPHA,
    WIDTH_DEFAULT,
    WIDTH_ERASER_MAX,
    WIDTH_MIN,
    WIDTH_PEN_MAX,
)

_CONFIG_NAME = "settings.json"


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "glassboard"


def _config_path() -> Path:
    return _config_dir() / _CONFIG_NAME


def _read_settings() -> dict:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(updates: dict) -> None:
    """Merge updates into settings.json (preserves unrelated keys)."""
    data = _read_settings()
    data.update(updates)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _normalize_rgba(
    values: object,
) -> tuple[float, float, float, float] | None:
    if not isinstance(values, (list, tuple)) or len(values) not in (3, 4):
        return None
    try:
        r, g, b = (float(values[0]), float(values[1]), float(values[2]))
        a = float(values[3]) if len(values) == 4 else INK_ALPHA
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= c <= 1.0 for c in (r, g, b, a)):
        return None
    return (r, g, b, a)


def load_swatches() -> list[tuple[float, float, float, float]]:
    """Return four RGBA swatches, falling back to defaults on any error."""
    defaults = [tuple(c) for c in DEFAULT_SWATCHES]
    raw = _read_settings().get("swatches")
    if not isinstance(raw, list) or len(raw) != 4:
        return defaults
    parsed: list[tuple[float, float, float, float]] = []
    for item in raw:
        rgba = _normalize_rgba(item)
        if rgba is None:
            return defaults
        parsed.append(rgba)
    return parsed


def save_swatches(swatches: list[tuple[float, float, float, float]]) -> None:
    """Persist the four swatch colors."""
    if len(swatches) != 4:
        raise ValueError("expected exactly 4 swatches")
    _write_settings(
        {"swatches": [[round(c, 4) for c in rgba] for rgba in swatches]}
    )


def load_tool_widths() -> dict[str, float]:
    """Return pen/eraser stroke widths, clamped to each tool's range."""
    defaults = {"pen": WIDTH_DEFAULT, "eraser": WIDTH_DEFAULT}
    raw = _read_settings().get("tool_widths")
    if not isinstance(raw, dict):
        return dict(defaults)
    out = dict(defaults)
    limits = {"pen": WIDTH_PEN_MAX, "eraser": WIDTH_ERASER_MAX}
    for key, ceiling in limits.items():
        if key not in raw:
            continue
        try:
            width = float(raw[key])
        except (TypeError, ValueError):
            continue
        out[key] = max(WIDTH_MIN, min(ceiling, width))
    return out


def save_tool_widths(widths: dict[str, float]) -> None:
    """Persist pen/eraser stroke widths."""
    pen = max(WIDTH_MIN, min(WIDTH_PEN_MAX, float(widths["pen"])))
    eraser = max(WIDTH_MIN, min(WIDTH_ERASER_MAX, float(widths["eraser"])))
    _write_settings(
        {
            "tool_widths": {
                "pen": round(pen, 2),
                "eraser": round(eraser, 2),
            }
        }
    )
