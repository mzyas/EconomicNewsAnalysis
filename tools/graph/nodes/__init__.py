"""Graph nodes for the economic news analysis workflow.

Each node is a pure function that takes the current ``NewsAnalysisState``
and returns a partial update. The LangGraph framework merges the update
into the running state. Errors that bubble out of a node are caught and
recorded on ``state['errors']`` so a single bad item never blocks the
rest of the pipeline.

The nodes live in per-stage modules (``fetch``, ``ranking``, ``analysis``,
``quality``, ``briefing``, ``delivery``, ``runlog``); this package re-exports
them so ``workflow.py`` and callers keep importing from ``tools.graph.nodes``.
Patch collaborators (``send_email``, ``batch_fetch`` ...) in the stage module
that uses them, e.g. ``tools.graph.nodes.delivery.send_email``.
"""

from __future__ import annotations

from ...analyzer import analyze_items
from ...evidence_fetcher import fetch_evidence
from ._artifacts import (
    _has_valid_html as _has_valid_html,
    _has_valid_markdown as _has_valid_markdown,
    _pipeline_status as _pipeline_status,
)
from .analysis import analyze_with_llm_node, collect_evidence_node
from .briefing import build_briefing_node, trend_table_node, write_outputs_node
from .delivery import deliver_email_node
from .fetch import (
    fetch_feeds_node,
    fetch_market_data_node,
    google_news_resolve_node,
    list_sources_node,
    load_config_node,
    movers_news_node,
)
from .quality import quality_gate_node
from .ranking import rank_items_node
from .runlog import check_log_node, write_run_log_node

__all__ = [
    "analyze_with_llm_node",
    "build_briefing_node",
    "check_log_node",
    "collect_evidence_node",
    "deliver_email_node",
    "fetch_feeds_node",
    "fetch_market_data_node",
    "google_news_resolve_node",
    "list_sources_node",
    "load_config_node",
    "movers_news_node",
    "quality_gate_node",
    "rank_items_node",
    "trend_table_node",
    "write_outputs_node",
    "write_run_log_node",
    "fetch_evidence",
    "analyze_items",
]
