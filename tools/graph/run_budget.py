"""Serializable deadline and stage-checkpoint helpers for one graph run."""

from __future__ import annotations

import time
from typing import Any


def create_run_budget(total_seconds: int | float, *, now: float | None = None) -> dict[str, Any]:
    """Create the shared monotonic deadline state for one daily run."""
    total = max(1, int(total_seconds))
    started = time.monotonic() if now is None else float(now)
    reserve = min(60, max(1, int(total * 0.1)))
    deadline = started + total
    return {
        "total_seconds": total,
        "reserve_seconds": reserve,
        "started_monotonic": started,
        "deadline_monotonic": deadline,
        "finalize_deadline_monotonic": deadline - reserve,
        "remaining_seconds": total,
        "degraded": False,
        "degradation_reason": None,
        "blocked_stage": None,
        "blocked_item": None,
        "checkpoints": [],
    }


def _remaining_seconds(budget: dict[str, Any], now: float) -> float:
    return max(0.0, round(float(budget["deadline_monotonic"]) - now, 3))


def begin_stage(
    budget: dict[str, Any],
    stage: str,
    *,
    completed_count: int,
    target_count: int,
    active_item: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Append a stable checkpoint for a stage that is about to start."""
    started = time.monotonic() if now is None else float(now)
    checkpoint = {
        "stage": stage,
        "status": "running",
        "started_monotonic": started,
        "completed_monotonic": None,
        "duration_seconds": None,
        "completed_count": max(0, int(completed_count)),
        "target_count": max(0, int(target_count)),
        "active_item": active_item,
        "remaining_seconds": _remaining_seconds(budget, started),
    }
    budget.setdefault("checkpoints", []).append(checkpoint)
    budget["active_stage"] = stage
    budget["active_item"] = active_item
    budget["remaining_seconds"] = checkpoint["remaining_seconds"]
    return checkpoint


def complete_stage(
    budget: dict[str, Any],
    stage: str,
    *,
    completed_count: int,
    now: float | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Close the current stage checkpoint with progress and elapsed time."""
    completed = time.monotonic() if now is None else float(now)
    checkpoints = budget.setdefault("checkpoints", [])
    checkpoint = next(
        (
            entry
            for entry in reversed(checkpoints)
            if entry.get("stage") == stage and entry.get("status") == "running"
        ),
        None,
    )
    if checkpoint is None:
        checkpoint = begin_stage(
            budget,
            stage,
            completed_count=completed_count,
            target_count=completed_count,
            now=completed,
        )
    checkpoint["status"] = status
    checkpoint["completed_monotonic"] = completed
    checkpoint["duration_seconds"] = round(
        max(0.0, completed - float(checkpoint["started_monotonic"])), 3
    )
    checkpoint["completed_count"] = max(0, int(completed_count))
    checkpoint["remaining_seconds"] = _remaining_seconds(budget, completed)
    budget["remaining_seconds"] = checkpoint["remaining_seconds"]
    budget["active_stage"] = None
    budget["active_item"] = None
    return checkpoint


def should_stop_optional_work(budget: dict[str, Any], *, now: float | None = None) -> bool:
    """Stop non-essential enrichment once finalization reserve is reached."""
    current = time.monotonic() if now is None else float(now)
    remaining = _remaining_seconds(budget, current)
    budget["remaining_seconds"] = remaining
    if current < float(budget["finalize_deadline_monotonic"]):
        return False

    budget["degraded"] = True
    budget["degradation_reason"] = (
        "budget_exhausted" if remaining <= 0 else "finalization_reserve"
    )
    budget["blocked_stage"] = budget.get("active_stage")
    budget["blocked_item"] = budget.get("active_item")
    return True


def public_run_summary(budget: dict[str, Any]) -> dict[str, Any]:
    """Return stable, serializable budget fields for logs and runtime output."""
    fields = (
        "total_seconds", "reserve_seconds", "remaining_seconds", "degraded",
        "degradation_reason", "blocked_stage", "blocked_item", "checkpoints",
    )
    return {field: budget.get(field) for field in fields}