"""Nodes that render the briefing, the trend table and the output files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...briefing_builder import SEPARATOR, build_daily_briefing, build_daily_briefing_html
from ...markdown_writer import write_markdown_artifact
from ...trend_table import build_trend_rows, render_html, render_markdown
from ._artifacts import (
    _has_valid_html,
    _has_valid_markdown,
    _partial_report_notice,
    _pipeline_status,
)
from ._common import _append_errors
from .quality import _derive_mainlines, _market_impact_from_items

logger = logging.getLogger(__name__)


def _build_briefing_unbudgeted(state: dict[str, Any]) -> dict[str, Any]:
    """Compose the daily briefing markdown + html from items + analyses."""
    items = list(state.get("ranked_items") or state.get("items") or [])
    analyses = list(state.get("analysis", []))
    daily = state.get("daily", {}) or {}
    market_impact = daily.get("market_impact")
    daily_mainlines = daily.get("mainlines")
    weekly_watchlist = daily.get("weekly_watchlist")
    # Optional上游合成的中美日宏观背景（国家码 → 文本）。缺省时 builder 退回到
    # 按国别归并官方条目的确定性版本。
    macro_background = daily.get("macro_background")

    if not daily_mainlines:
        daily_mainlines = _derive_mainlines(items, analyses)
    if market_impact is None:
        market_impact = _market_impact_from_items(items, analyses) or None

    markdown = build_daily_briefing(
        items,
        analyses,
        market_impact=market_impact,
        daily_mainlines=daily_mainlines,
        daily_weekly_watchlist=weekly_watchlist,
        daily_macro_background=macro_background,
    )
    html = build_daily_briefing_html(
        items,
        analyses,
        market_impact=market_impact,
        daily_mainlines=daily_mainlines,
        daily_weekly_watchlist=weekly_watchlist,
        daily_macro_background=macro_background,
    )

    # Prepend the trend table to the canonical briefing markdown here (the
    # single place that owns briefing_markdown), so the markdown attachment
    # written by write_outputs_node AND the stdout/result all include it.
    # The HTML email keeps rendering the trend table from trend_rows
    # separately, so we deliberately do NOT touch ``html`` here.
    trend_markdown = state.get("trend_markdown") or ""
    if trend_markdown:
        markdown = trend_markdown.strip() + "\n\n" + SEPARATOR + "\n\n" + markdown

    pipeline_status = _pipeline_status(
        state,
        has_content=_has_valid_markdown(markdown) or _has_valid_html(html),
    )
    warnings = list(state.get("warnings", []))
    if pipeline_status == "partial_success":
        notice_md, notice_html, warning = _partial_report_notice(state)
        markdown = notice_md + "\n\n" + markdown
        html = notice_html + html
        if warning not in warnings:
            warnings.append(warning)

    return {
        "briefing_markdown": markdown,
        "briefing": {"markdown": markdown, "html": html},
        "mainlines": list(daily_mainlines or []),
        "daily": {
            **daily,
            "mainlines": list(daily_mainlines or []),
            "market_impact": market_impact or {},
            "weekly_watchlist": list(weekly_watchlist or []),
        },
        "pipeline_status": pipeline_status,
        "warnings": warnings,
        "_phase": "briefed",
    }


def build_briefing_node(state: dict[str, Any]) -> dict[str, Any]:
    update = _build_briefing_unbudgeted(state)
    budget = state.get("_run_budget") or {}
    if budget.get("degraded"):
        notice = (
            "> **Budget-degraded briefing**: optional work stopped at "
            f"{budget.get('blocked_stage') or 'unknown'} ({budget.get('blocked_item') or 'unknown'}).\n"
            "> Evidence or quality checks may be incomplete; conclusions have lower confidence.\n\n"
        )
        update["briefing_markdown"] = notice + update.get("briefing_markdown", "")
        update["briefing"]["markdown"] = update["briefing_markdown"]
        update["warnings"] = list(update.get("warnings", [])) + [
            "Budget-degraded briefing delivered."
        ]
    return update


def trend_table_node(state: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic trend table from market data — no RSS, no LLM.

    The node reads ``state['market_context']['snapshots']`` (populated by
    ``fetch_market_data_node``), derives direction symbols, and renders
    both Markdown and HTML versions of the trend table.

    If market data is disabled or all snapshots failed the node returns a
    ``no_data`` status so downstream nodes can adjust accordingly.
    """
    market_context = state.get("market_context", {}) or {}
    # Individual stocks only trigger mover news searches; keep them out of the table.
    snapshots = [
        snap
        for snap in market_context.get("snapshots", [])
        if snap.get("asset_class") != "equity_stock"
    ]

    if not snapshots:
        return {
            "trend_table": {"rows": [], "status": "no_data", "error": "没有市场数据快照"},
            "_phase": "trend_table_built",
        }

    try:
        rows = build_trend_rows(snapshots)
    except Exception as exc:
        logger.exception("build_trend_rows failed")
        return {
            "trend_table": {
                "rows": [],
                "status": "failed",
                "error": f"趋势表构建失败: {exc}",
            },
            "_phase": "trend_table_built",
            "errors": _append_errors(
                state,
                [{"type": "TrendTableError", "message": str(exc)}],
            ),
        }

    # Determine the most recent as_of across all successful snapshots
    as_of = None
    for snap in snapshots:
        snap_as_of = snap.get("as_of")
        if snap_as_of:
            if as_of is None or snap_as_of > as_of:
                as_of = snap_as_of

    markdown = render_markdown(rows, as_of=as_of)
    html = render_html(rows, as_of=as_of)

    n_success = sum(1 for r in rows if r.status == "success")
    n_total = len(rows)
    status = "success"
    if n_success == 0:
        status = "failed"
    elif n_success < n_total:
        status = "partial"

    return {
        "trend_table": {
            "rows": [r.to_dict() for r in rows],
            "markdown": markdown,
            "html": html,
            "status": status,
            "as_of": as_of,
            "error": None,
        },
        "trend_markdown": markdown,
        "_phase": "trend_table_built",
    }


def write_outputs_node(state: dict[str, Any]) -> dict[str, Any]:
    """Write only business Markdown; final Runtime JSON is written later."""
    config = state.get("config", {}) or {}
    output_dir = config.get("output_dir") or state.get("output_dir")
    markdown_output = state.get("briefing_markdown") or state.get("trend_markdown")
    briefing = state.get("briefing") or {}
    html_output = briefing.get("html") if isinstance(briefing, dict) else ""
    pipeline_status = _pipeline_status(
        state,
        has_content=_has_valid_markdown(markdown_output) or _has_valid_html(html_output),
    )
    if not output_dir:
        return {
            "output_files": dict(state.get("output_files", {})),
            "output_status": "skipped",
            "pipeline_status": pipeline_status,
            "_phase": "outputs_written",
        }
    topic = str(state.get("topic") or state.get("mode") or config.get("topic") or "briefing")
    merged = dict(state.get("output_files", {}))
    if not _has_valid_markdown(markdown_output):
        return {
            "output_files": merged,
            "output_status": "skipped",
            "pipeline_status": pipeline_status,
            "_phase": "outputs_written",
        }
    warnings = list(state.get("warnings", []))
    try:
        markdown_path = write_markdown_artifact(Path(output_dir), topic, str(markdown_output))
        if not markdown_path.is_file() or markdown_path.stat().st_size == 0:
            raise OSError("Markdown 产物写入后不存在或为空")
        merged["markdown"] = str(markdown_path)
        output_status = "success"
    except OSError as exc:
        warnings.append(f"Markdown 产物写入失败: {exc}")
        output_status = "failed"
    return {
        "warnings": warnings,
        "output_files": merged,
        "output_status": output_status,
        "pipeline_status": pipeline_status,
        "_phase": "outputs_written",
    }
