"""Run-budget wrapper shared by the budget-aware nodes."""

from __future__ import annotations

from typing import Any

from ..run_budget import (
    begin_stage as _budget_begin,
    complete_stage as _budget_complete,
    should_stop_optional_work as _budget_stop,
)


def _budgeted(state: dict[str, Any], stage: str, target: int, active: str | None, callback):
    budget = state.get("_run_budget")
    if not isinstance(budget, dict):
        return callback()
    _budget_begin(budget, stage, completed_count=0, target_count=target, active_item=active)
    if _budget_stop(budget):
        _budget_complete(budget, stage, completed_count=0, status="skipped")
        return {
            "_run_budget": budget,
            "warnings": list(state.get("warnings", []))
            + [f"Budget downgrade: {stage} stopped at {active or 'stage start'}."],
        }
    update = callback()
    completed = len(update.get("analysis", update.get("_evidence", update.get("items", []))))
    _budget_complete(budget, stage, completed_count=completed)
    return {**update, "_run_budget": budget}
