"""Compact tool-result summary for the scheduled ``deliver`` / ``check`` modes.

The full runtime result (items, analyses, briefing markdown, ...) is already
written to ``<run_id>_runtime.json``. The Cron agent that calls the tool only
needs the status fields, so returning the whole payload (hundreds of KB on one
line) overflows Hermes' inline limit and the agent cannot read it back.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SUMMARY_MODES = frozenset({"deliver", "check"})

_STATUS_FIELDS = (
    "run_id",
    "mode",
    "pipeline_status",
    "output_status",
    "delivery_status",
    "delivery_kind",
    "delivery_requested",
    "email_send_attempted",
    "delivery_skip_reason",
    "email_sent",
    "effective_delivery_config",
    "started_at",
    "completed_at",
)
_RUN_SUMMARY_FIELDS = (
    "total_seconds",
    "reserve_seconds",
    "remaining_seconds",
    "degraded",
    "degradation_reason",
    "blocked_stage",
    "blocked_item",
)
_CHECKPOINT_FIELDS = ("stage", "status", "duration_seconds")

_TOP_N = 5
_SAMPLE_ERRORS = 10
_MESSAGE_LIMIT = 300


def _clip(value: Any, limit: int = _MESSAGE_LIMIT) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summarize_errors(errors: list[Any]) -> dict[str, Any]:
    entries = [e if isinstance(e, dict) else {"message": e} for e in errors]
    by_type = Counter(str(e.get("type", "unknown")) for e in entries)
    grouped = Counter((str(e.get("type", "unknown")), _clip(e.get("message", ""))) for e in entries)
    return {
        "total": len(entries),
        "by_type": dict(by_type.most_common()),
        "top_messages": [
            {"type": kind, "message": message, "count": count}
            for (kind, message), count in grouped.most_common(_TOP_N)
        ],
        "first": [
            {**e, "message": _clip(e.get("message", ""))} if "message" in e else e
            for e in entries[:_SAMPLE_ERRORS]
        ],
    }


def _summarize_llm_audit(audit: list[Any]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for entry in audit:
        if isinstance(entry, dict):
            counts[
                (
                    str(entry.get("node", "")),
                    str(entry.get("provider", "")),
                    str(entry.get("model", "")),
                )
            ] += 1
    return [
        {"node": node, "provider": provider, "model": model, "count": count}
        for (node, provider, model), count in counts.items()
    ]


def _summarize_analyses(analyses: list[Any]) -> dict[str, Any]:
    entries = [a for a in analyses if isinstance(a, dict)]
    # ``analysis_method`` stays "heuristic_draft" even after a successful LLM
    # call, so success is read from the enrichment flag and ``llm_error``.
    failed = [a for a in entries if a.get("llm_error") or a.get("requires_agent_enrichment", True)]
    reasons = Counter(_clip(a.get("llm_error")) for a in entries if a.get("llm_error"))
    return {
        "total": len(entries),
        "llm_ok": len(entries) - len(failed),
        "llm_failed": len(failed),
        "with_translated_title": sum(1 for a in entries if a.get("translated_title")),
        "llm_error_reasons": [
            {"message": message, "count": count} for message, count in reasons.most_common(_TOP_N)
        ],
    }


def _summarize_run_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    summary = {k: run_summary[k] for k in _RUN_SUMMARY_FIELDS if k in run_summary}
    summary["checkpoints"] = [
        {k: checkpoint.get(k) for k in _CHECKPOINT_FIELDS}
        for checkpoint in run_summary.get("checkpoints") or []
        if isinstance(checkpoint, dict)
    ]
    return summary


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce a full runtime result to a few KB, status fields first."""
    summary: dict[str, Any] = {"result_kind": "summary"}
    summary.update({k: result[k] for k in _STATUS_FIELDS if k in result})
    output_files = result.get("output_files")
    if isinstance(output_files, dict):
        summary["output_files"] = output_files
        if output_files.get("json"):
            summary["full_result_path"] = output_files["json"]
    for key in ("statistics", "warnings", "check"):
        if key in result:
            summary[key] = result[key]
    if isinstance(result.get("run_summary"), dict):
        summary["run_summary"] = _summarize_run_summary(result["run_summary"])
    summary["errors"] = _summarize_errors(list(result.get("errors") or []))
    summary["llm_audit"] = _summarize_llm_audit(list(result.get("llm_audit") or []))
    summary["analyses"] = _summarize_analyses(list(result.get("analyses") or []))
    return summary


__all__ = ["SUMMARY_MODES", "summarize_result"]
