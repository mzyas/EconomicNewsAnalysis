"""Market-mover news: turn unusual price moves into targeted news searches.

The trend table says *that* a stock fell; this module finds out *why* by
building a Google News RSS query for each symbol whose move crosses a
threshold and fetching the headlines. It is deterministic apart from the
network fetch, and every failure degrades to "no items" plus an error entry.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import asdict
from typing import Any

from .feed_fetcher_core import Source, fetch_rss
from .normalizer import normalize_news_items

MOVER_CATEGORY = "market_mover"
MOVER_SOURCE_PREFIX = "google_news_mover_"

DEFAULT_DAY_THRESHOLD_PCT = 4.0
DEFAULT_WEEK_THRESHOLD_PCT = 8.0
DEFAULT_MAX_SYMBOLS = 5
DEFAULT_ITEMS_PER_SYMBOL = 3
DEFAULT_ASSET_CLASSES = ("equity_stock", "equity_index")
DEFAULT_WINDOW = "2d"


def _movers_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("movers")
    return raw if isinstance(raw, dict) else {}


def movers_enabled(config: dict[str, Any]) -> bool:
    return bool(_movers_config(config).get("enabled", True))


def select_movers(
    snapshots: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the snapshots whose 1-day or 1-week move crosses a threshold.

    Only successful ``price`` snapshots of the configured asset classes are
    considered (yields move in bp, not %). Results are ordered by the size of
    the larger move and capped at ``max_symbols``.
    """
    cfg = _movers_config(config)
    day_limit = float(cfg.get("day_threshold_pct", DEFAULT_DAY_THRESHOLD_PCT))
    week_limit = float(cfg.get("week_threshold_pct", DEFAULT_WEEK_THRESHOLD_PCT))
    max_symbols = int(cfg.get("max_symbols", DEFAULT_MAX_SYMBOLS))
    asset_classes = set(cfg.get("asset_classes") or DEFAULT_ASSET_CLASSES)

    movers: list[tuple[float, dict[str, Any]]] = []
    for snap in snapshots:
        if snap.get("status") != "success" or snap.get("asset_type", "price") != "price":
            continue
        if snap.get("asset_class") not in asset_classes:
            continue
        day = snap.get("change_pct")
        week = snap.get("change_1w_pct")
        day_hit = day is not None and abs(day) >= day_limit
        week_hit = week is not None and abs(week) >= week_limit
        if not (day_hit or week_hit):
            continue
        magnitude = max(
            abs(day) / day_limit if day_hit else 0.0,
            abs(week) / week_limit if week_hit else 0.0,
        )
        movers.append((magnitude, snap))

    movers.sort(key=lambda pair: pair[0], reverse=True)
    return [snap for _, snap in movers[: max(0, max_symbols)]]


def describe_trigger(snap: dict[str, Any]) -> str:
    parts = []
    if snap.get("change_pct") is not None:
        parts.append(f"日 {snap['change_pct']:+.1f}%")
    if snap.get("change_1w_pct") is not None:
        parts.append(f"周 {snap['change_1w_pct']:+.1f}%")
    return f"{snap.get('name', '')} " + " / ".join(parts)


def build_query_url(snap: dict[str, Any], window: str = DEFAULT_WINDOW) -> str:
    name = str(snap.get("name", "")).strip()
    suffix = " stock" if snap.get("asset_class") == "equity_stock" else ""
    query = f'"{name}"{suffix} when:{window}'
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def fetch_mover_items(
    snapshots: list[dict[str, Any]],
    config: dict[str, Any],
    connect_timeout: int,
    read_timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch and normalize news for every mover. Returns ``(items, errors)``."""
    cfg = _movers_config(config)
    per_symbol = int(cfg.get("items_per_symbol", DEFAULT_ITEMS_PER_SYMBOL))
    window = str(cfg.get("window", DEFAULT_WINDOW))

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for snap in select_movers(snapshots, config):
        symbol_id = str(snap.get("symbol_id", ""))
        source = Source(
            id=f"{MOVER_SOURCE_PREFIX}{symbol_id}",
            name=f"Google News: {snap.get('name', symbol_id)}",
            type="google_news_rss",
            url=build_query_url(snap, window),
            region=str(snap.get("region", "")),
            language="en",
            category=MOVER_CATEGORY,
            tags=["market_mover", symbol_id],
        )
        raw, error = fetch_rss(source, per_symbol, connect_timeout, read_timeout)
        if error:
            errors.append({"type": "MoverFetchError", "id": source.id, "message": error})
            continue
        trigger = describe_trigger(snap)
        for item in normalize_news_items([asdict(it) for it in raw]):
            item["mover"] = {
                "symbol_id": symbol_id,
                "name": snap.get("name", ""),
                "change_1d_pct": snap.get("change_pct"),
                "change_1w_pct": snap.get("change_1w_pct"),
                "trigger": trigger,
            }
            items.append(item)
    return items, errors
