"""Persistent user settings (swatch colors, etc.)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from glassboard.canvas import DEFAULT_SWATCHES, INK_ALPHA

_CONFIG_NAME = "settings.json"


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "glassboard"


def _config_path() -> Path:
    return _config_dir() / _CONFIG_NAME


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
    path = _config_path()
    if not path.is_file():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    raw = data.get("swatches") if isinstance(data, dict) else None
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
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "swatches": [[round(c, 4) for c in rgba] for rgba in swatches],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
