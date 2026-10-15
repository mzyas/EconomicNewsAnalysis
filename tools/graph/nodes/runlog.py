"""Nodes that write and inspect the run log."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config_loader import ROOT
from ...delivery_trace import effective_delivery_config
from ...run_log import append_log, evaluate_fetch_window
from ._common import _now_iso


def _write_run_log_unbudgeted(state: dict[str, Any]) -> dict[str, Any]:
    """Append a JSONL log entry when ``config['log_path']`` is configured."""
    config = state.get("config", {}) or {}
    log_path = config.get("log_path")
    if not log_path:
        return {"_phase": "logged"}
    items = list(state.get("ranked_items") or state.get("items") or [])
    entry = {
        "run_id": state.get("run_id", ""),
        "mode": state.get("mode", ""),
        "status": state.get("pipeline_status", "success"),
        "pipeline_status": state.get("pipeline_status", "success"),
        "output_status": state.get("output_status", "skipped"),
        "delivery_status": state.get("delivery_status", "skipped"),
        "delivery_kind": state.get("delivery_kind", "none"),
        "email_sent": bool(state.get("email_sent", False)),
        "delivery_requested": bool(state.get("delivery_requested", False)),
        "email_send_attempted": bool(state.get("email_send_attempted", False)),
        "delivery_skip_reason": state.get("delivery_skip_reason"),
        "effective_delivery_config": effective_delivery_config(config),
        "started_at": state.get("started_at", ""),
        "completed_at": state.get("completed_at") or _now_iso(),
        "fetched_at": state.get("started_at", ""),
        "sources_successful": state.get("_sources_successful", 0),
        "sources_failed": state.get("_sources_failed", 0),
        "sources_skipped": state.get("_sources_skipped", 0),
        "items_fetched": state.get("_items_fetched", 0),
        "items_normalized": len(state.get("items", [])),
        "items_ranked": len(items),
        "items_analyzed": len(state.get("analysis", [])),
        "item_urls": [
            i.get("url") or i.get("link") or i.get("id", "")
            for i in items
            if i.get("url") or i.get("link") or i.get("id")
        ],
    }
    append_log(Path(log_path), entry)
    return {"_run_log_path": str(log_path), "_phase": "logged"}


def write_run_log_node(state: dict[str, Any]) -> dict[str, Any]:
    """Persist a separate stable checkpoint summary beside the legacy log entry."""
    update = _write_run_log_unbudgeted(state)
    budget = state.get("_run_budget")
    log_path = (state.get("config") or {}).get("log_path")
    if isinstance(budget, dict) and log_path:
        append_log(
            Path(log_path),
            {
                "record_type": "stage_checkpoint_summary",
                "run_id": state.get("run_id", ""),
                "budget": budget,
                "completed_at": _now_iso(),
            },
        )
    return {**update, **({"_run_budget": budget} if isinstance(budget, dict) else {})}


def check_log_node(state: dict[str, Any]) -> dict[str, Any]:
    """Inspect the runtime log to decide whether to fetch today."""
    config = state.get("config", {}) or {}
    log_path = Path(config.get("log_path") or ROOT / "logs" / "runtime.jsonl")
    check = evaluate_fetch_window(log_path)
    return {"check": check, "_phase": "checked"}
