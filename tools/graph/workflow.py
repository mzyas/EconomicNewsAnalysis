"""LangGraph workflow definition for the economic news analysis skill.

This module wires the graph nodes declared in :mod:`tools.graph.nodes`
into a single compiled :class:`langgraph.graph.StateGraph`. The graph
honours the same ``mode`` values that the legacy runtime supports
(``fetch``, ``analyze``, ``briefing``, ``deliver``, ``check`` and
``list-sources``) and exposes a single ``run_workflow`` entry point
that returns a public-only projection of the final state.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ..delivery_trace import effective_delivery_config

from langgraph.graph import END, START, StateGraph

from .checkpoint import (
    create_checkpoint_saver,
    load_latest_state,
    prune_checkpoints,
)
from .nodes import (
    _has_valid_markdown,
    _pipeline_status,
    analyze_with_llm_node,
    build_briefing_node,
    check_log_node,
    collect_evidence_node,
    deliver_email_node,
    fetch_feeds_node,
    fetch_market_data_node,
    google_news_resolve_node,
    list_sources_node,
    load_config_node,
    movers_news_node,
    quality_gate_node,
    rank_items_node,
    trend_table_node,
    write_outputs_node,
    write_run_log_node,
)
from .state import NewsAnalysisState, initial_state
from .llm_runtime import clear_run_hermes_llm, set_run_hermes_llm

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Conditional edge helpers ───────────────────────────────────────────────


def _after_load_config(state: dict[str, Any]) -> str:
    """Pick the first node based on the requested mode."""
    mode = state.get("mode", "briefing")
    if mode == "check":
        return "check_log"
    if mode == "list-sources":
        return "list_sources"
    return "fetch_market_data"


def _after_rank(state: dict[str, Any]) -> str:
    """``fetch`` ends after ranking; ``trend`` should never reach here;
    every other mode needs evidence."""
    return "end_after_rank" if state.get("mode") == "fetch" else "collect_evidence"


def _after_analyze(state: dict[str, Any]) -> str:
    """``analyze`` finishes after the LLM call; deeper modes hit the gate."""
    return "end_after_analyze" if state.get("mode") == "analyze" else "quality_gate"


def _after_market_data(state: dict[str, Any]) -> str:
    """After market data is fetched, always go through trend_table first.
    The ``_after_trend_table`` helper routes onward based on mode."""
    return "trend_table"


def _after_trend_table(state: dict[str, Any]) -> str:
    """After the trend table is built, route appropriately:
    * ``trend`` mode → ``write_outputs`` (skip RSS/LLM entirely).
    * Everything else → ``fetch_feeds`` (continue to news pipeline)."""
    return "write_outputs" if state.get("mode") == "trend" else "fetch_feeds"


def _should_retry_or_downgrade(state: dict[str, Any]) -> str:
    """Decide whether to retry evidence collection or downgrade.

    * ``continue`` — quality gate passed, proceed to ``build_briefing``.
    * ``retry`` — first failure, give evidence collection one more try.
    * ``downgrade`` — second (or later) failure, build the briefing
      from whatever is available.
    """
    gate = state.get("_quality_gate") or {}
    if gate.get("passed"):
        return "continue"
    retry_count = int(state.get("_retry_count", 0))
    config = state.get("config") or {}
    graph_cfg = config.get("graph", {}) if isinstance(config, dict) else {}
    max_retries = int(graph_cfg.get("max_quality_retries", 1))
    if retry_count < max_retries:
        return "retry"
    return "downgrade"


def _should_deliver(state: dict[str, Any]) -> str:
    """``deliver`` mode routes to email; ``trend`` mode routes to email
    when ``config.deliver_email`` is True; everything else goes straight
    to the run log."""
    mode = state.get("mode", "")
    if mode == "deliver":
        return "deliver_email"
    if mode == "trend":
        config = state.get("config", {}) or {}
        if config.get("deliver_email"):
            return "deliver_email"
    return "write_run_log"


# ── Graph assembly ─────────────────────────────────────────────────────────


def build_workflow(
    checkpoint_path: str | None = None,
    saver: Any | None = None,
) -> Any:
    """Build the compiled LangGraph workflow.

    Args:
        checkpoint_path: Optional path to a SQLite checkpoint database.
        saver: Optional pre-built ``SqliteSaver`` instance. When supplied
            the caller is responsible for its lifecycle. When only
            ``checkpoint_path`` is set, a fresh saver is created here
            and stored on the graph so ``run_workflow`` can close it.

    Returns:
        A compiled LangGraph graph exposing ``invoke`` / ``stream``.
    """
    workflow = StateGraph(NewsAnalysisState)

    workflow.add_node("load_config", load_config_node)
    workflow.add_node("fetch_market_data", fetch_market_data_node)
    workflow.add_node("fetch_feeds", fetch_feeds_node)
    workflow.add_node("movers_news", movers_news_node)
    workflow.add_node("google_news_resolve", google_news_resolve_node)
    workflow.add_node("rank_items", rank_items_node)
    workflow.add_node("collect_evidence", collect_evidence_node)
    workflow.add_node("analyze_with_llm", analyze_with_llm_node)
    workflow.add_node("quality_gate", quality_gate_node)
    workflow.add_node("build_briefing", build_briefing_node)
    workflow.add_node("write_outputs", write_outputs_node)
    workflow.add_node("deliver_email", deliver_email_node)
    workflow.add_node("write_run_log", write_run_log_node)
    workflow.add_node("check_log", check_log_node)
    workflow.add_node("list_sources", list_sources_node)
    workflow.add_node("trend_table", trend_table_node)

    workflow.set_entry_point("load_config")
    workflow.add_conditional_edges(
        "load_config",
        _after_load_config,
        {
            "check_log": "check_log",
            "list_sources": "list_sources",
            "fetch_market_data": "fetch_market_data",
        },
    )

    workflow.add_conditional_edges(
        "fetch_market_data",
        _after_market_data,
        {
            "trend_table": "trend_table",
        },
    )
    workflow.add_conditional_edges(
        "trend_table",
        _after_trend_table,
        {
            "fetch_feeds": "fetch_feeds",
            "write_outputs": "write_outputs",
        },
    )
    workflow.add_edge("fetch_feeds", "movers_news")
    workflow.add_edge("movers_news", "google_news_resolve")
    workflow.add_edge("google_news_resolve", "rank_items")
    workflow.add_conditional_edges(
        "rank_items",
        _after_rank,
        {
            "end_after_rank": END,
            "collect_evidence": "collect_evidence",
        },
    )

    workflow.add_edge("collect_evidence", "analyze_with_llm")
    workflow.add_conditional_edges(
        "analyze_with_llm",
        _after_analyze,
        {
            "end_after_analyze": END,
            "quality_gate": "quality_gate",
        },
    )

    workflow.add_conditional_edges(
        "quality_gate",
        _should_retry_or_downgrade,
        {
            "continue": "build_briefing",
            "retry": "collect_evidence",
            "downgrade": "build_briefing",
        },
    )

    workflow.add_edge("build_briefing", "write_outputs")
    workflow.add_conditional_edges(
        "write_outputs",
        _should_deliver,
        {
            "deliver_email": "deliver_email",
            "write_run_log": "write_run_log",
        },
    )
    workflow.add_edge("deliver_email", "write_run_log")
    workflow.add_edge("write_run_log", END)
    workflow.add_edge("check_log", END)
    workflow.add_edge("list_sources", END)

    owns_saver = False
    if saver is None and checkpoint_path:
        saver = create_checkpoint_saver(checkpoint_path)
        owns_saver = True

    compiled = workflow.compile(checkpointer=saver)
    if owns_saver:
        compiled._runtime_checkpoint_saver = saver  # type: ignore[attr-defined]
    return compiled


# ── Public result projection ───────────────────────────────────────────────


def _project_result(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Convert the graph state to a payload that matches ``runtime_result.schema.json``."""
    trace_config = state.get("effective_delivery_config")
    trace_config = trace_config if isinstance(trace_config, dict) else effective_delivery_config(config)
    public: dict[str, Any] = {
        "run_id": state.get("run_id", ""),
        "mode": state.get("mode", config.get("mode", "briefing")),
        "pipeline_status": state.get("pipeline_status", "success"),
        "output_status": state.get("output_status", "skipped"),
        "delivery_status": state.get("delivery_status", "skipped"),
        "delivery_requested": bool(state.get("delivery_requested", False)),
        "email_send_attempted": bool(state.get("email_send_attempted", False)),
        "delivery_skip_reason": state.get("delivery_skip_reason"),
        "effective_delivery_config": dict(trace_config),
        "delivery_kind": state.get("delivery_kind", "none"),
        "started_at": state.get("started_at", _now_iso()),
        "completed_at": state.get("completed_at") or _now_iso(),
        "items": list(state.get("ranked_items") or state.get("items", [])),
        "analyses": list(state.get("analysis", [])),
        "llm_audit": list(state.get("llm_audit", [])),
        "errors": list(state.get("errors", [])),
        "warnings": list(state.get("warnings", [])),
        "output_files": dict(state.get("output_files", {})),
    }
    public["status"] = public["pipeline_status"]
    public["skill"] = config.get(
        "_skill",
        {"name": "economic-news-analysis", "version": str(config.get("version", "0.5.0"))},
    )
    public["output_language"] = config.get("output_language", "zh-CN")

    for optional in (
        "market_context",
        "skipped_sources",
        "sources",
        "check",
        "briefing_markdown",
        "analysis_markdown",
        "trend_markdown",
        "email_sent",
        "trend_table",
    ):
        if optional in state:
            public[optional] = state[optional]

    if "mainlines" in state:
        public["daily_mainlines"] = state["mainlines"]
    daily = state.get("daily")
    if isinstance(daily, dict):
        if "weekly_watchlist" in daily:
            public["daily_weekly_watchlist"] = daily["weekly_watchlist"]
        if "market_impact" in daily:
            public["daily_market_impact"] = daily["market_impact"]

    public["statistics"] = {
        "sources_successful": int(state.get("_sources_successful", 0)),
        "sources_failed": int(state.get("_sources_failed", 0)),
        "sources_skipped": int(state.get("_sources_skipped", 0)),
        "items_fetched": int(state.get("_items_fetched", 0)),
        "items_normalized": len(public.get("items", [])),
        "items_ranked": len(state.get("ranked_items", [])),
        "items_analyzed": len(public.get("analyses", [])),
    }
    return public


def _actual_markdown_file(path_value: Any) -> bool:
    if not path_value:
        return False
    path = Path(str(path_value))
    try:
        return (
            path.is_file()
            and path.stat().st_size > 0
            and _has_valid_markdown(path.read_text(encoding="utf-8-sig"))
        )
    except (OSError, UnicodeError):
        return False


def _output_status(expected: set[str], successful: set[str]) -> str:
    if not expected:
        return "skipped"
    count = len(expected & successful)
    if count == len(expected):
        return "success"
    return "partial_success" if count else "failed"


def _write_final_runtime_json(
    result: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Atomically write the final public Runtime result after delivery."""
    finalized = dict(result)
    finalized["output_files"] = dict(result.get("output_files", {}))
    finalized["warnings"] = list(result.get("warnings", []))
    mode = str(finalized.get("mode", config.get("mode", "")))
    output_dir = config.get("output_dir")
    has_business_content = (
        mode in {"briefing", "deliver", "trend"}
        and finalized.get("pipeline_status") in {"success", "partial_success"}
    )
    if not output_dir or not has_business_content:
        finalized["output_status"] = "skipped"
        return finalized

    expected = {"json"}
    successful: set[str] = set()
    markdown_expected = _has_valid_markdown(
        finalized.get("briefing_markdown") or finalized.get("trend_markdown")
    )
    if markdown_expected:
        expected.add("markdown")
        if _actual_markdown_file(finalized["output_files"].get("markdown")):
            successful.add("markdown")

    safe_run_id = re.sub(r"[^\w.-]+", "-", str(finalized.get("run_id") or "runtime"))
    json_path = Path(output_dir) / f"{safe_run_id}_runtime.json"
    temp_path = json_path.with_suffix(json_path.suffix + ".tmp")
    finalized["output_files"]["json"] = str(json_path)
    successful.add("json")
    finalized["output_status"] = _output_status(expected, successful)

    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(finalized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(json_path)
    except OSError as exc:
        successful.discard("json")
        finalized["output_files"].pop("json", None)
        finalized["output_status"] = _output_status(expected, successful)
        finalized["warnings"].append(f"最终 Runtime JSON 写入失败: {exc}")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return finalized


# ── Entry points ──────────────────────────────────────────────────────────


def _resolve_checkpoint_path(config: dict[str, Any]) -> str | None:
    graph_cfg = config.get("graph") or {}
    if not isinstance(graph_cfg, dict):
        return None
    path = graph_cfg.get("checkpoint_path")
    return str(path) if path else None


def _close_saver(compiled: Any) -> None:
    saver = getattr(compiled, "_runtime_checkpoint_saver", None)
    if saver is not None:
        try:
            saver.conn.close()
        except Exception:
            logger.exception("failed to close checkpoint saver")
        finally:
            try:
                delattr(compiled, "_runtime_checkpoint_saver")
            except AttributeError:
                pass


def run_workflow(
    config: dict[str, Any],
    mode: str | None = None,
    run_id: str | None = None,
    resume: bool = False,
    hermes_llm: Any | None = None,
    model_routes: dict[str, dict[str, Any]] | None = None,
    strict_model_routes: bool = True,
) -> dict[str, Any]:
    """Execute the workflow and return the public-only result payload.

    Args:
        config: Runtime configuration (post ``load_runtime_config``).
        mode: Optional override for ``config['mode']``.
        run_id: Optional identifier used for checkpointing and the
            public ``run_id`` field.
        resume: When ``True``, an existing checkpoint for ``run_id`` is
            loaded instead of starting fresh.

    Returns:
        A dict matching ``schemas/runtime_result.schema.json``.
    """
    effective_mode = mode or config.get("mode", "briefing")
    config = dict(config)
    config["mode"] = effective_mode

    # ``ctx.llm`` is a live Hermes facade and must not enter checkpointed
    # config.  Routes are plain plugin settings and are safe to checkpoint.
    graph_cfg = dict(config.get("graph") or {})
    injected_llm = hermes_llm or graph_cfg.pop("_hermes_llm", None)
    graph_cfg.pop("_credential_provider", None)
    if model_routes is not None:
        graph_cfg["model_routes"] = dict(model_routes)
    graph_cfg.setdefault("model_routes", {})
    graph_cfg["strict_model_routes"] = strict_model_routes
    config["graph"] = graph_cfg
    checkpoint_path = _resolve_checkpoint_path(config)
    state: dict[str, Any]
    if resume and run_id and checkpoint_path:
        loaded = load_latest_state(checkpoint_path, run_id)
        if loaded:
            state = dict(loaded)
            state["config"] = config
            state["mode"] = effective_mode
        else:
            state = initial_state(config, run_id=run_id)
    else:
        state = initial_state(config, run_id=run_id)

    graph = build_workflow(checkpoint_path=checkpoint_path)
    thread_id = str(state.get("run_id", ""))
    # Hand the facade to nodes only for the duration of this run.
    if injected_llm is not None:
        set_run_hermes_llm(thread_id, injected_llm)
    # Cap supersteps as a runaway backstop. The longest legitimate path (deliver
    # mode) is ~13 nodes + 3 per quality-gate retry, so a margin above
    # ``13 + 3 * max_quality_retries`` allows real runs while aborting any
    # unexpected cycle far below LangGraph's default of 25. Configurable via
    # ``graph.recursion_limit``.
    graph_cfg = config.get("graph", {}) if isinstance(config, dict) else {}
    max_retries = int(graph_cfg.get("max_quality_retries", 1))
    recursion_limit = int(graph_cfg.get("recursion_limit", 16 + 4 * max(1, max_retries)))
    thread_config = {
        "recursion_limit": recursion_limit,
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "",
        },
    }

    try:
        try:
            final_state = graph.invoke(state, config=thread_config)
        except Exception as exc:
            logger.exception("graph invocation failed")
            final_state = dict(state)
            final_state["errors"] = list(final_state.get("errors", [])) + [
                {"type": type(exc).__name__, "message": str(exc)}
            ]
            final_state["pipeline_status"] = "failed"
    finally:
        clear_run_hermes_llm(thread_id)
        _close_saver(graph)

    # Bound checkpoint-DB growth: each run is one thread that is never reclaimed,
    # so without pruning the SQLite file grows without limit. Keep only the most
    # recent threads (enough to ``resume`` a recent run). Best-effort — a prune
    # failure must never fail an otherwise-successful run.
    if checkpoint_path:
        keep_last = int(graph_cfg.get("checkpoint_keep_last", 20))
        try:
            prune_checkpoints(checkpoint_path, keep_last)
        except Exception:
            logger.exception("checkpoint prune failed")

    final_state.setdefault("mode", effective_mode)
    final_state.setdefault("started_at", state.get("started_at", _now_iso()))
    if not final_state.get("completed_at"):
        final_state["completed_at"] = _now_iso()
    if final_state.get("pipeline_status") == "running":
        if effective_mode in {"briefing", "deliver", "trend"}:
            briefing = final_state.get("briefing") or {}
            has_content = _has_valid_markdown(
                final_state.get("briefing_markdown")
                or final_state.get("trend_markdown")
                or (briefing.get("markdown") if isinstance(briefing, dict) else "")
            )
            final_state["pipeline_status"] = _pipeline_status(
                final_state, has_content=has_content
            )
        else:
            final_state["pipeline_status"] = (
                "partial_success" if final_state.get("errors") else "success"
            )

    return _write_final_runtime_json(_project_result(final_state, config), config)


__all__ = [
    "build_workflow",
    "run_workflow",
    "_should_retry_or_downgrade",
    "_should_deliver",
    "_write_final_runtime_json",
    "_after_market_data",
]

# Budget-aware public projection and retry routing.
from .run_budget import public_run_summary as _public_run_summary

_should_retry_or_downgrade_unbudgeted = _should_retry_or_downgrade
_project_result_unbudgeted = _project_result


def _should_retry_or_downgrade(state: dict[str, Any]) -> str:
    if (state.get("_run_budget") or {}).get("degraded"):
        return "downgrade"
    return _should_retry_or_downgrade_unbudgeted(state)


def _project_result(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    result = _project_result_unbudgeted(state, config)
    budget = state.get("_run_budget")
    if isinstance(budget, dict):
        result["run_summary"] = _public_run_summary(budget)
    return result