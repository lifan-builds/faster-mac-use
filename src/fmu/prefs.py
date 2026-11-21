"""User preferences shared by the Python runtime and the fmu-ax menu bar app (`~/.fmu/prefs.json`)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_cache: tuple[float, dict[str, Any]] = (-1.0, {})


def prefs_path() -> Path:
    return Path(os.environ.get("FMU_PREFS") or Path.home() / ".fmu" / "prefs.json")


def load_prefs() -> dict[str, Any]:
    global _cache
    try:
        mtime = prefs_path().stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache[0]:
        try:
            data = json.loads(prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache = (mtime, data if isinstance(data, dict) else {})
    return _cache[1]
