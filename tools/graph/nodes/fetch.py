"""Nodes that load configuration and fetch market data, feeds and movers."""

from __future__ import annotations

import logging
from typing import Any

from ...feed_fetcher import fetch_from_config, source_inventory
from ...google_news_enrichment import enrich_google_news_items
from ...market_data import build_market_context
from ...movers import fetch_mover_items, movers_enabled
from ...normalizer import normalize_news_items
from ..events import record_event
from ._budget import _budgeted
from ._common import _append_errors

logger = logging.getLogger(__name__)


def load_config_node(state: dict[str, Any]) -> dict[str, Any]:
    """Record that the run has started and set the initial phase."""
    record_event(state, "load_config", "completed")
    return {"_phase": "loaded", "pipeline_status": "running"}


def fetch_market_data_node(state: dict[str, Any]) -> dict[str, Any]:
    """Fetch market data if the user enabled it; otherwise keep the skipped default."""
    config = state.get("config", {}) or {}
    try:
        context = build_market_context(config)
    except Exception as exc:
        logger.exception("fetch_market_data failed")
        context = {
            "enabled": bool(config.get("market_data", {}).get("enabled", False)),
            "provider": config.get("market_data", {}).get("provider"),
            "status": "failed",
            "snapshots": [],
            "errors": [{"type": type(exc).__name__, "message": str(exc)}],
        }
    return {"market_context": context, "_phase": "market_fetched"}


def _fetch_feeds_unbudgeted(state: dict[str, Any]) -> dict[str, Any]:
    """Fetch RSS feeds and merge with any input items already in state."""
    config = state.get("config", {}) or {}
    mode = state.get("mode", "briefing")
    raw_items: list[dict[str, Any]] = list(state.get("items", []))
    if config.get("input_items"):
        raw_items.extend(config.get("input_items", []))
    stdin_text = config.get("stdin_text") or state.get("stdin_text")
    if stdin_text:
        raw_items.append({"title": "标准输入新闻", "summary": stdin_text, "source": "stdin"})

    sources_successful = 0
    sources_failed = 0
    sources_skipped = 0
    items_fetched = 0
    fetch_errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    should_fetch = mode in {"fetch", "briefing", "deliver"} and config.get("fetch_enabled", True)
    if should_fetch:
        try:
            fetch_result = fetch_from_config(config)
        except Exception as exc:
            logger.exception("fetch_from_config failed")
            fetch_errors.append({"type": "FetchError", "message": str(exc)})
            fetch_result = {
                "items": [],
                "total_items": 0,
                "sources_successful": 0,
                "sources_failed": 0,
                "sources_skipped": 0,
                "errors": [],
                "skipped": [],
            }
        raw_items.extend(fetch_result.get("items", []))
        sources_successful = int(fetch_result.get("sources_successful", 0))
        sources_failed = int(fetch_result.get("sources_failed", 0))
        sources_skipped = int(fetch_result.get("sources_skipped", 0))
        items_fetched = int(fetch_result.get("total_items", 0))
        fetch_errors.extend(fetch_result.get("errors", []))
        skipped = list(fetch_result.get("skipped", []))

    try:
        items = normalize_news_items(raw_items)
    except Exception as exc:
        logger.exception("normalize_news_items failed")
        items = raw_items
        fetch_errors.append({"type": "NormalizeError", "message": str(exc)})

    return {
        "items": items,
        "_items_fetched": items_fetched,
        "_sources_successful": sources_successful,
        "_sources_failed": sources_failed,
        "_sources_skipped": sources_skipped,
        "skipped_sources": skipped,
        "errors": _append_errors(state, fetch_errors),
        "_phase": "fetched",
    }


def fetch_feeds_node(state: dict[str, Any]) -> dict[str, Any]:
    return _budgeted(state, "rss", 1, "RSS feeds", lambda: _fetch_feeds_unbudgeted(state))


def movers_news_node(state: dict[str, Any]) -> dict[str, Any]:
    """Search news for symbols whose price move crossed the configured threshold."""
    config = state.get("config", {}) or {}
    mode = state.get("mode", "briefing")
    if (
        mode not in {"briefing", "deliver"}
        or not config.get("fetch_enabled", True)
        or not movers_enabled(config)
    ):
        return {"_phase": "movers_skipped"}

    snapshots = list((state.get("market_context") or {}).get("snapshots", []))
    try:
        mover_items, errors = fetch_mover_items(
            snapshots,
            config,
            int(config.get("connect_timeout", 10)),
            int(config.get("read_timeout", 40)),
        )
    except Exception as exc:
        logger.exception("movers_news failed")
        return {
            "_phase": "movers_fetched",
            "errors": _append_errors(state, [{"type": "MoverError", "message": str(exc)}]),
        }

    items = list(state.get("items", []))
    seen = {str(item.get("id", "")) for item in items}
    new_items = [item for item in mover_items if item["id"] not in seen]
    update: dict[str, Any] = {"items": items + new_items, "_phase": "movers_fetched"}
    if errors:
        update["errors"] = _append_errors(state, errors)
    return update


def google_news_resolve_node(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve Google News discovery links without dropping unverified entries."""
    items = list(state.get("items", []))
    if not items:
        return {"items": items, "_phase": "google_resolved"}
    config = state.get("config") or {}
    connect_timeout = int(config.get("connect_timeout", 10))
    read_timeout = int(config.get("read_timeout", 40))
    try:
        resolved, errors = enrich_google_news_items(items, connect_timeout, read_timeout)
    except Exception as exc:
        logger.exception("google_news_resolve failed")
        return {
            "items": items,
            "_phase": "google_resolved",
            "errors": _append_errors(
                state, [{"type": "GoogleNewsResolveError", "message": str(exc)}]
            ),
        }
    return {
        "items": resolved,
        "_phase": "google_resolved",
        "errors": _append_errors(state, errors),
    }


def list_sources_node(state: dict[str, Any]) -> dict[str, Any]:
    """List available sources without performing any network calls."""
    sources = source_inventory(state.get("config", {}) or {})
    return {"sources": sources, "_phase": "listed"}
