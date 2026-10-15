"""State definition for the economic news analysis graph."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
import sys
from typing import Any

from ..delivery_trace import effective_delivery_config
from .run_budget import create_run_budget

if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:
    from typing_extensions import NotRequired, TypedDict


class NewsAnalysisState(TypedDict, total=False):
    """LangGraph state for the economic news analysis workflow.

    Public fields mirror ``runtime_result.schema.json`` so the state can be
    projected into the legacy runtime result. Internal fields (prefixed with
    ``_``) are private to the graph and never written to public output.
    """

    run_id: NotRequired[str]
    mode: NotRequired[str]
    config: NotRequired[dict[str, Any]]
    stdin_text: NotRequired[str | None]
    started_at: NotRequired[str]
    completed_at: NotRequired[str | None]
    status: NotRequired[str]
    pipeline_status: NotRequired[str]
    output_status: NotRequired[str]
    delivery_status: NotRequired[str]
    delivery_kind: NotRequired[str]
    items: NotRequired[list[dict[str, Any]]]
    error_items: NotRequired[list[dict[str, Any]]]
    ranked_items: NotRequired[list[dict[str, Any]]]
    analysis: NotRequired[list[dict[str, Any]]]
    llm_audit: NotRequired[list[dict[str, str]]]
    briefing: NotRequired[dict[str, Any]]
    evidence: NotRequired[list[dict[str, Any]]]
    mainlines: NotRequired[list[dict[str, Any]]]
    daily: NotRequired[dict[str, Any]]
    delivery: NotRequired[dict[str, Any]]
    meta: NotRequired[dict[str, Any]]
    output_dir: NotRequired[str | None]
    errors: NotRequired[list[dict[str, Any]]]
    warnings: NotRequired[list[str]]
    market_context: NotRequired[dict[str, Any]]
    output_files: NotRequired[dict[str, str]]
    briefing_markdown: NotRequired[str | None]
    analysis_markdown: NotRequired[str | None]
    trend_markdown: NotRequired[str | None]
    trend_table: NotRequired[dict[str, Any]]
    sources: NotRequired[list[dict[str, Any]]]
    check: NotRequired[dict[str, Any]]
    skipped_sources: NotRequired[list[dict[str, Any]]]
    delivery_requested: NotRequired[bool]
    email_send_attempted: NotRequired[bool]
    delivery_skip_reason: NotRequired[str | None]
    effective_delivery_config: NotRequired[dict[str, Any]]
    email_sent: NotRequired[bool]
    _phase: NotRequired[str]
    _evidence: NotRequired[list[dict[str, Any]]]
    _quality_gate: NotRequired[dict[str, Any]]
    _retry_count: NotRequired[int]
    _delivery_approved: NotRequired[bool]
    _events: NotRequired[list[dict[str, Any]]]
    _research_queue: NotRequired[list[dict[str, Any]]]
    _run_budget: NotRequired[dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_state(
    config: dict[str, Any],
    stdin_text: str | None = None,
    run_id: str | None = None,
) -> NewsAnalysisState:
    """Build the initial graph state for a new or resumed run.

    The returned mapping contains every public field of the legacy
    ``runtime_result.schema.json`` plus internal fields prefixed with ``_``
    that drive the graph execution. Internal fields must never be projected
    into the public output.
    """

    resolved_run_id = run_id or str(uuid.uuid4())
    delivery_config = effective_delivery_config(config)
    return NewsAnalysisState(
        run_id=resolved_run_id,
        mode=config.get("mode", "briefing"),
        config=dict(config),
        stdin_text=stdin_text,
        started_at=_now_iso(),
        completed_at=None,
        pipeline_status="running",
        output_status="skipped",
        delivery_status="skipped",
        delivery_kind="none",
        email_sent=False,
        delivery_requested=bool(delivery_config["deliver_email"]),
        email_send_attempted=False,
        delivery_skip_reason=None,
        effective_delivery_config=delivery_config,
        items=[],
        error_items=[],
        ranked_items=[],
        analysis=[],
        llm_audit=[],
        briefing={},
        evidence=[],
        mainlines=[],
        daily={},
        delivery={},
        meta={"graph": True},
        output_dir=config.get("output_dir"),
        errors=[],
        warnings=[],
        market_context={
            "enabled": bool(config.get("market_data", {}).get("enabled", False)),
            "provider": config.get("market_data", {}).get("provider"),
            "status": "skipped",
            "snapshots": [],
            "errors": [],
        },
        output_files={},
        _phase="init",
        _evidence=[],
        _quality_gate={},
        _retry_count=0,
        _delivery_approved=False,
        _events=[],
        _research_queue=[],
        _run_budget=create_run_budget(config.get("daily_time_budget_seconds", 600)),
    )
