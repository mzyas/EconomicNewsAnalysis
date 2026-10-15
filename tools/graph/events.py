"""Node execution event logger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_VALID_STATUSES = {"entered", "completed", "failed", "skipped"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(
    state: dict[str, Any],
    node: str,
    status: str,
    error_count: int = 0,
) -> list[dict[str, Any]]:
    """Append a node execution event to ``state['_events']``.

    Events are stored on the internal ``_events`` channel only and are
    excluded from the public runtime result projection.
    """

    if status not in _VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
        )
    events: list[dict[str, Any]] = list(state.get("_events", []))
    started_at = _now_iso()
    events.append(
        {
            "run_id": state.get("run_id", ""),
            "node": node,
            "status": status,
            "started_at": started_at,
            "ended_at": _now_iso(),
            "error_count": int(error_count),
        }
    )
    state["_events"] = events
    return events
