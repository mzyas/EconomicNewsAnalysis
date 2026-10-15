"""Small helpers shared by every graph node module."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_errors(state: dict[str, Any], new_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge new error dicts onto ``state['errors']`` without losing earlier entries."""
    merged: list[dict[str, Any]] = list(state.get("errors", []))
    merged.extend(new_errors)
    return merged
