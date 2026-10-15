"""Nodes that collect evidence and run the per-item LLM analysis."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...analyzer import analyses_to_markdown, analyze_item
from ...evidence_fetcher import batch_fetch
from ._budget import _budgeted
from ._common import _append_errors
from ._llm import _new_research_adapter, _resolve_hermes_llm

logger = logging.getLogger(__name__)


_AGGREGATOR_HOST_HINTS = (
    "news.google.com",
    "google_news",
    "rssaggregator",
    "feedburner",
)


def _url_priority(url: str) -> int:
    lowered = (url or "").lower()
    if not lowered:
        return 3
    for hint in _AGGREGATOR_HOST_HINTS:
        if hint in lowered:
            return 2
    return 0


def _collect_evidence_unbudgeted(
    state: dict[str, Any],
    research_max_items: int | None = None,
) -> dict[str, Any]:
    """Collect evidence for the top high-priority items.

    Only ``research_max_items`` items are processed. When the caller does
    not pass an explicit value (as is the case when LangGraph invokes the
    node with ``state`` only), the limit is read from
    ``config.graph.research_max_items`` so the configured value is honoured
    instead of being silently ignored; it defaults to 5. Items whose URL
    points at a known aggregator are sorted below original publisher URLs
    so that fetches prefer the original article. Per-item failures are
    captured in ``state['errors']`` and do not abort the rest of the batch.
    """
    config = state.get("config", {}) or {}
    graph_cfg = config.get("graph", {}) if isinstance(config, dict) else {}
    if not graph_cfg.get("evidence_enabled", True):
        return {"_evidence": [], "_phase": "evidence_collected"}

    if research_max_items is None:
        research_max_items = int(graph_cfg.get("research_max_items", 5))

    candidates = list(state.get("ranked_items") or state.get("items") or [])
    if not candidates:
        return {"_evidence": [], "_phase": "evidence_collected"}

    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            _url_priority(str(item.get("url", ""))),
            -float(item.get("relevance_score", 0.0)),
        ),
    )
    selected = sorted_candidates[: max(0, int(research_max_items))]

    logger.debug(
        "collect_evidence selected=%d urls=%s",
        len(selected),
        [str(item.get("url", ""))[:80] for item in selected],
    )

    timeout = int(config.get("timeout_seconds", 15))
    evidence: list[dict[str, Any]] = []
    new_errors: list[dict[str, Any]] = []
    for item in selected:
        url = str(item.get("url", ""))
        item_id = str(item.get("id", ""))
        if not url:
            continue
        try:
            results = batch_fetch([url], max_items=1, timeout=timeout)
        except Exception as exc:
            logger.exception("batch_fetch failed for %s", url)
            new_errors.append(
                {
                    "type": "EvidenceFetchError",
                    "id": item_id,
                    "url": url,
                    "message": f"unexpected error: {exc}",
                }
            )
            continue
        for entry in results:
            evidence.append(
                {
                    "item_id": item_id,
                    "url": entry.get("url", url),
                    "content": entry.get("content"),
                    "error": entry.get("error"),
                    "truncated": bool(entry.get("truncated", False)),
                }
            )
            if entry.get("error"):
                _has_content = "YES" if entry.get("content") else "NO"
                logger.warning(
                    "EVIDENCE_FETCH url=%s error=%s has_content=%s",
                    url[:80],
                    entry.get("error", "")[:80],
                    _has_content,
                )
                new_errors.append(
                    {
                        "type": "EvidenceFetchError",
                        "id": item_id,
                        "url": entry.get("url", url),
                        "message": str(entry["error"]),
                    }
                )

    public_evidence = [{k: v for k, v in entry.items() if k != "item_id"} for entry in evidence]
    return {
        "_evidence": evidence,
        "evidence": public_evidence,
        "_phase": "evidence_collected",
        "errors": _append_errors(state, new_errors),
    }


def collect_evidence_node(
    state: dict[str, Any], research_max_items: int | None = None
) -> dict[str, Any]:
    target = research_max_items or int(
        (state.get("config", {}).get("graph", {}) or {}).get("research_max_items", 5)
    )
    return _budgeted(
        state,
        "evidence",
        target,
        "next evidence URL",
        lambda: _collect_evidence_unbudgeted(state, research_max_items),
    )


def _build_source_context(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source", {}) or {}
    return {
        "title": str(item.get("title", "")),
        "summary": str(item.get("summary", "")),
        "url": str(item.get("url", "")),
        "published_at": str(item.get("published_at", "")),
        "source_name": str(source.get("name", "")),
        "source_country": str(source.get("country", "")),
        "source_language": str(source.get("language", "")),
    }


def _analyze_unbudgeted(state: dict[str, Any]) -> dict[str, Any]:
    """Run heuristic + LLM analysis on ranked items.

    Items without usable evidence keep their heuristic draft and
    ``requires_agent_enrichment=True``. LLM failures degrade gracefully
    in the same way so the downstream briefing is still buildable.
    """
    config = state.get("config", {}) or {}
    graph_cfg = config.get("graph", {}) if isinstance(config, dict) else {}
    items = list(state.get("ranked_items") or state.get("items") or [])
    evidence = list(state.get("_evidence", []))
    evidence_by_item: dict[str, list[dict[str, Any]]] = {}
    for entry in evidence:
        evidence_by_item.setdefault(str(entry.get("item_id", "")), []).append(entry)

    language = str(config.get("output_language", "zh-CN"))
    focus_assets = list(config.get("focus_assets", []) or [])

    llm_audit = state.setdefault("llm_audit", [])
    adapter = _new_research_adapter(
        graph_cfg,
        _resolve_hermes_llm(graph_cfg, state),
        llm_audit,
    )

    def _analyze_one(item: dict[str, Any]) -> dict[str, Any]:
        """Heuristic draft refined by one LLM call. Never raises."""
        item_id = str(item.get("id", ""))
        item_evidence = evidence_by_item.get(item_id, [])
        draft = analyze_item(item, focus_assets, language)
        try:
            llm_result = adapter.analyze(
                title=str(item.get("title", "")),
                source_context=_build_source_context(item),
                evidence=item_evidence,
                relevance_score=float(item.get("relevance_score", 0.0)),
                language=language,
            )
        except Exception as exc:
            logger.exception("llm analyze call failed for %s", item_id)
            draft["llm_error"] = str(exc)
            draft["error"] = f"llm error: {exc}"
            return draft
        if llm_result.get("requires_agent_enrichment"):
            draft["llm_error"] = llm_result.get("error")
            if llm_result.get("error"):
                draft["error"] = llm_result["error"]
            return draft
        if llm_result.get("analysis"):
            draft["analysis"] = llm_result["analysis"]
        if llm_result.get("signals"):
            draft["signals"] = llm_result["signals"]
        if llm_result.get("focus_assets"):
            draft["focus_assets"] = llm_result["focus_assets"]
        if llm_result.get("translated_title"):
            draft["translated_title"] = llm_result["translated_title"]
        if llm_result.get("translated_summary"):
            draft["translated_summary"] = llm_result["translated_summary"]
        draft["requires_agent_enrichment"] = False
        return draft

    # The per-item LLM calls are independent and I/O-bound, so running them
    # concurrently turns ~N sequential round-trips into ~N/concurrency,
    # which is the main lever against the 300s delivery timeout. Order is
    # preserved by ``executor.map``. Concurrency is configurable and falls
    # back to a conservative default that stays well under provider limits.
    concurrency = max(1, int(graph_cfg.get("llm_concurrency", 4)))
    if items and concurrency > 1:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as pool:
            analyses: list[dict[str, Any]] = list(pool.map(_analyze_one, items))
    else:
        analyses = [_analyze_one(item) for item in items]

    update: dict[str, Any] = {"analysis": analyses, "_phase": "analyzed"}
    if state.get("mode") == "analyze" and config.get("output_format") == "markdown":
        update["analysis_markdown"] = analyses_to_markdown(analyses)
    return update


def analyze_with_llm_node(state: dict[str, Any]) -> dict[str, Any]:
    target = len(state.get("ranked_items") or state.get("items") or [])
    return _budgeted(
        state, "analysis", target, "next analysis item", lambda: _analyze_unbudgeted(state)
    )
