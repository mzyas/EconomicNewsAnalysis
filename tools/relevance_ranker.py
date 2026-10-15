"""Deterministic macro-news relevance scoring."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .source_quality import assess_grounding_status


def _parse_dt(value: Any) -> datetime | None:
    """Best-effort parse of a published_at string (ISO datetime or date-only)."""
    s = str(value or "").strip()
    if not s:
        return None
    for candidate in (s.replace("Z", "+00:00"), s[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _recency_delta(published_at: Any, now: datetime | None = None) -> tuple[float, str | None]:
    """Score adjustment by article age. Fresh items gain, stale items lose.

    Returns ``(delta, reason)``. Missing/unparseable dates are neutral (0.0) —
    many sources omit a usable date, so we neither reward nor punish them here.
    """
    dt = _parse_dt(published_at)
    if dt is None:
        return 0.0, None
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400.0
    if age_days <= 1:
        return 0.10, "最新发布(24h内)"
    if age_days <= 3:
        return 0.06, "近3日发布"
    if age_days <= 7:
        return 0.02, "近一周发布"
    if age_days <= 14:
        return 0.0, None
    if age_days <= 30:
        return -0.06, "发布超两周降权"
    if age_days <= 90:
        return -0.12, "发布超一月降权"
    return -0.20, "陈旧内容降权"

HIGH_SIGNAL_TAGS = {
    "monetary_policy",
    "interest_rate",
    "inflation",
    "employment",
    "gdp_growth",
    "financial_stability",
    "fx_intervention",
    "geopolitics",
    "energy",
}
OFFICIAL_SOURCE_IDS = {
    "fed_press_all",
    "fed_fomc",
    "fed_speeches",
    "ecb_rss",
    "boj_rss",
    "pboc_rss",
    "nbs_releases",
    "nbs_analysis",
    "fsa_rss",
    # Japan government/fiscal/statistics — previously only boj/fsa earned the
    # official boost, which systematically under-scored Japanese官方 items
    # relative to the US (fed×3) and China (pboc + nbs×2).
    "mof_japan",
    "cabinet_office_rss",
    "meti_statistics_rss",
}
CURRENT_SIGNAL_PATTERNS = (
    r"\b(?:cpi|ppi|gdp|pmi|m[12])\b",
    r"\b(?:rate decision|policy decision|employment report|jobs report)\b",
    r"(?:统计数据报告|金融统计|社会融资|社融|利率决议|货币政策决定)",
    r"(?:同比|环比).{0,24}(?:增长|下降|上升|回落)",
    # Japanese-language current-signal cues. Without these, ja-language items
    # (BOJ, ja Google News) could never match the +0.18 boost the English and
    # Chinese cues grant, dragging Japan down the ranking.
    r"(?:消費者物価|物価指数|金融政策決定会合|日銀短観|賃金|雇用統計|国内総生産|鉱工業生産|貿易統計)",
    r"(?:前年同月比|前期比).{0,24}(?:上昇|低下|増加|減少|拡大|縮小)",
)
RETROSPECTIVE_PATTERNS = (
    r"十四五",
    r"(?:回顾|综述|成就)(?:报告|总结|系列)?",
    r"\b(?:retrospective|year in review|anniversary)\b",
)
DISCOVERY_SOURCE_PREFIXES = ("google_news_",)
MIN_DISCOVERY_SCORE = 0.3


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_discovery_item(item: dict[str, Any]) -> bool:
    discovery = item.get("discovery", {})
    return (
        isinstance(discovery, dict) and discovery.get("channel") == "google_news"
    ) or str(item.get("source", {}).get("id", "")).startswith(
        DISCOVERY_SOURCE_PREFIXES
    )


def score_item(item: dict[str, Any], focus_assets: list[str] | None = None) -> dict[str, Any]:
    focus_assets = focus_assets or []
    tags = {str(tag).lower() for tag in item.get("tags", [])}
    text = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("summary", "")),
            " ".join(tags),
        ]
    ).lower()

    score = 0.15
    reasons: list[str] = []
    matched_high_signal = sorted(tags & HIGH_SIGNAL_TAGS)
    if matched_high_signal:
        score += min(0.45, 0.12 * len(matched_high_signal))
        reasons.append(f"高信号标签: {', '.join(matched_high_signal)}")

    source_id = str(item.get("source", {}).get("id", ""))
    if source_id in OFFICIAL_SOURCE_IDS:
        score += 0.25
        reasons.append("官方来源")

    if _matches_any(text, CURRENT_SIGNAL_PATTERNS):
        score += 0.18
        reasons.append("包含当期数据或政策信号")

    if _matches_any(text, RETROSPECTIVE_PATTERNS):
        score -= 0.3
        reasons.append("回顾性或总结性内容降权")

    grounding_status = assess_grounding_status(
        str(item.get("title", "")),
        str(item.get("summary", "")),
    )
    if grounding_status in {"title_only", "insufficient_source_context"}:
        score -= 0.2
        reasons.append("来源上下文不足")

    matched_assets = [
        asset for asset in focus_assets if str(asset).lower() in text
    ]
    if matched_assets:
        score += min(0.15, 0.05 * len(matched_assets))
        reasons.append(f"匹配关注资产: {', '.join(matched_assets)}")

    recency_delta, recency_reason = _recency_delta(item.get("published_at"))
    score += recency_delta
    if recency_reason:
        reasons.append(recency_reason)
    score = round(max(0.0, min(score, 1.0)), 3)

    ranked = dict(item)
    ranked["relevance_score"] = score
    ranked["ranking_reasons"] = reasons or ["一般宏观相关性"]
    return ranked


def rank_items(
    items: list[dict[str, Any]],
    focus_assets: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    ranked = [score_item(item, focus_assets) for item in items]
    ranked.sort(
        key=lambda item: (
            item["relevance_score"],
            item.get("published_at", ""),
            item.get("title", ""),
        ),
        reverse=True,
    )
    if limit is None or len(ranked) <= limit:
        return ranked

    selected = ranked[:limit]
    discovery_candidates = [
        item
        for item in ranked
        if _is_discovery_item(item)
        and item["relevance_score"] >= MIN_DISCOVERY_SCORE
    ]
    target_discovery = min(4, max(1, limit // 3), len(discovery_candidates))
    selected_ids = {item["id"] for item in selected}
    selected_discovery = [
        item
        for item in selected
        if _is_discovery_item(item)
    ]
    missing = target_discovery - len(selected_discovery)
    if missing > 0:
        additions = [
            item for item in discovery_candidates if item["id"] not in selected_ids
        ][:missing]
        replaceable = [
            item
            for item in reversed(selected)
            if not _is_discovery_item(item)
        ][:len(additions)]
        replace_ids = {item["id"] for item in replaceable}
        selected = [item for item in selected if item["id"] not in replace_ids]
        for item in additions:
            item["ranking_reasons"] = [
                *item["ranking_reasons"],
                "来源多样性保留",
            ]
            selected.append(item)
        selected.sort(
            key=lambda item: (
                item["relevance_score"],
                item.get("published_at", ""),
                item.get("title", ""),
            ),
            reverse=True,
        )
    return selected


def _item_country(item: dict[str, Any]) -> str:
    """Best-effort country code for an item (e.g. ``CN`` / ``US`` / ``JP``)."""
    return str(item.get("source", {}).get("country") or "").strip()


def balance_by_country(
    ranked: list[dict[str, Any]],
    quota: dict[str, int] | None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Re-select a score-sorted list so no single country crowds out the rest.

    ``ranked`` must already be sorted by descending relevance. ``quota`` maps a
    country code to the maximum number of items that country may contribute
    (a ``"default"`` key covers any country not listed; missing ⇒ unlimited).
    Items beyond a country's cap are skipped on the first pass; if that leaves
    the result under ``limit`` (because every remaining item hit a cap), the
    highest-scoring leftovers backfill the gap so the total count is preserved.

    With ``quota`` falsy the input is returned unchanged (backward compatible).
    """
    if not quota:
        return ranked
    cap_limit = limit if limit is not None else len(ranked)
    default_cap = quota.get("default")

    counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    for item in ranked:
        if len(selected) >= cap_limit:
            overflow.append(item)
            continue
        code = _item_country(item)
        cap = quota.get(code, default_cap)
        used = counts.get(code, 0)
        if cap is not None and used >= cap:
            overflow.append(item)
            continue
        counts[code] = used + 1
        selected.append(item)

    if len(selected) < cap_limit and overflow:
        for item in overflow[: cap_limit - len(selected)]:
            item["ranking_reasons"] = [
                *item.get("ranking_reasons", []),
                "国家配额回填",
            ]
            selected.append(item)
    return selected
