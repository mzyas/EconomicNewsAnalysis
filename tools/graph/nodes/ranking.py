"""Node that ranks and balances the normalized news items."""

from __future__ import annotations

import logging
from typing import Any

from ...relevance_ranker import balance_by_country, rank_items
from ._common import _append_errors

logger = logging.getLogger(__name__)


def rank_items_node(state: dict[str, Any]) -> dict[str, Any]:
    """Rank items by macroeconomic relevance with per-category quotas."""
    config = state.get("config", {}) or {}
    items = list(state.get("items", []))
    if not items:
        return {"ranked_items": [], "items": [], "_phase": "ranked"}
    try:
        from ...briefing_builder import _item_category

        graph_cfg = config.get("graph", {}) if isinstance(config, dict) else {}
        focus = list(config.get("focus_assets", []) or [])
        max_total = int(config.get("max_ranked_items", 20))

        # Per-section quotas live under ``graph`` (where the schema and example
        # configs declare them); fall back to a top-level key for backward
        # compatibility, then to a default. Reading only the top level (the old
        # behaviour) silently ignored ``graph.media_max_items`` etc.
        def _cap(key: str, default: int) -> int:
            return int(graph_cfg.get(key, config.get(key, default)))

        max_official = _cap("official_max_items", max_total)
        max_media = _cap("media_max_items", 8)
        max_tech = _cap("tech_max_items", 6)
        max_google = _cap("google_max_items", 6)
        max_hot = _cap("hot_max_items", 5)
        max_movers = _cap("movers_max_items", 6)

        # Optional per-country cap to stop one country (historically China)
        # from crowding out the rest. Shape: ``{"CN": 4, "US": 4, "JP": 4,
        # "default": 99}``. Absent ⇒ no balancing (backward compatible).
        country_balance = graph_cfg.get("country_balance") if isinstance(graph_cfg, dict) else None

        # Separate by category
        official_items = [i for i in items if _item_category(i) == "official"]
        media_items = [i for i in items if _item_category(i) == "media"]
        tech_items = [i for i in items if _item_category(i) == "tech"]
        google_items = [i for i in items if _item_category(i) == "google"]
        hot_items = [i for i in items if _item_category(i) == "hot"]
        mover_items = [i for i in items if _item_category(i) == "movers"]

        # Rank each group independently. When country-balancing is on, rank the
        # macro-bearing groups WITHOUT truncating first — otherwise the quota cut
        # would drop the minority-country items before balancing ever sees them,
        # and the per-country backfill would just restore the dominant country.
        ranked_official = rank_items(
            official_items, focus, None if country_balance else max_official
        )
        ranked_media = rank_items(media_items, focus, None if country_balance else max_media)
        ranked_tech = rank_items(tech_items, focus, max_tech)
        ranked_google = rank_items(google_items, focus, max_google)
        ranked_hot = rank_items(hot_items, focus, max_hot)
        ranked_movers = rank_items(mover_items, focus, max_movers)

        # Balance the country mix within the two macro-bearing groups (official
        # and media), applying the per-section limit here. Google News is
        # discovery-driven and already has its own diversity guard, so we leave
        # it untouched. Tech/hot are global (not country-split).
        if country_balance:
            ranked_official = balance_by_country(ranked_official, country_balance, max_official)
            ranked_media = balance_by_country(ranked_media, country_balance, max_media)

        # Merge, preserving order: official → media → tech → google → hot → movers
        # (movers last so existing section ordering and mainline picks are unchanged)
        ranked = (
            ranked_official
            + ranked_media
            + ranked_tech
            + ranked_google
            + ranked_hot
            + ranked_movers
        )
    except Exception as exc:
        logger.exception("rank_items failed")
        return {
            "ranked_items": items,
            "items": items,
            "_phase": "ranked",
            "errors": _append_errors(state, [{"type": "RankError", "message": str(exc)}]),
        }
    return {"ranked_items": ranked, "items": ranked, "_phase": "ranked"}
