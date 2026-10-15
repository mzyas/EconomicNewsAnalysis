"""Quality gate: auto-enrichment, mainlines and market-impact synthesis."""

from __future__ import annotations

import logging
from typing import Any

from ._budget import _budgeted
from ._common import _now_iso
from ._llm import _new_research_adapter, _resolve_hermes_llm

logger = logging.getLogger(__name__)


def _market_impact_from_items(
    items: list[dict[str, Any]], analyses: list[dict[str, Any]]
) -> dict[str, str]:
    """Derive a deterministic market impact stub when no human input is provided."""
    if not analyses:
        return {}
    return {
        "rates_bonds": "等待基于已核验来源、增长、通胀、政策预期和流动性数据分析。",
        "fx": "等待基于已核验来源、相对增长、利差和风险情绪分析。",
        "equities": "等待基于已核验来源、盈利、估值和融资成本分析。",
        "commodities": "等待基于已核验来源、供需、库存、美元和实际利率分析。",
        "risk_appetite": "仅说明非个性化风险暴露变化，不得给出仓位比例或买卖指令。",
    }


def _looks_untranslated(text: str) -> bool:
    """True when ``text`` has Latin letters but no CJK — i.e. not zh-CN.

    Used by the strict quality gate to detect titles the LLM failed to
    translate (the briefing's output language is zh-CN).
    """
    text = str(text or "")
    has_latin = any(c.isascii() and c.isalpha() for c in text)
    has_cjk = any("一" <= c <= "鿿" for c in text)
    return has_latin and not has_cjk


def _build_signals_digest(analyses: list[dict[str, Any]], limit: int = 10) -> str:
    """Flatten the day's signals into a compact bullet list for synthesis."""
    lines: list[str] = []
    for analysis in analyses:
        for signal in analysis.get("signals", []) or []:
            text = signal.get("signal", "") if isinstance(signal, dict) else str(signal)
            text = str(text).strip()
            if text:
                lines.append(f"- {text}")
            if len(lines) >= limit:
                return "\n".join(lines)
    return "\n".join(lines)


def _llm_market_impact(
    analyses: list[dict[str, Any]],
    graph_cfg: dict[str, Any],
    config: dict[str, Any],
    hermes_llm: Any | None = None,
    llm_audit: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """Synthesise a real market-impact map via one LLM call.

    Returns ``{}`` on empty digest or any failure so the caller falls back to
    the deterministic placeholder map.
    """
    digest = _build_signals_digest(analyses)
    if not digest:
        return {}
    try:
        adapter = _new_research_adapter(
            graph_cfg, hermes_llm, llm_audit if llm_audit is not None else []
        )
        return adapter.market_impact(digest, language=str(config.get("output_language", "zh-CN")))
    except Exception:
        logger.exception("market impact synthesis failed")
        return {}


def _build_official_digest(
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    per_country: int = 4,
) -> str:
    """Group official items by CN/US/JP into a compact per-country digest.

    Returns ``""`` when there are no CN/US/JP official items, so the caller
    skips the LLM call and the briefing falls back to its deterministic
    macro background.
    """
    from ...briefing_builder import _item_category, _title

    analysis_by_id = {str(a.get("news_item_id", "")): a for a in analyses}
    labels = {"CN": "中国", "US": "美国", "JP": "日本"}
    by_country: dict[str, list[str]] = {"CN": [], "US": [], "JP": []}
    for item in items:
        if _item_category(item) != "official":
            continue
        code = str(item.get("source", {}).get("country") or "").strip()
        if code not in by_country:
            continue
        analysis = analysis_by_id.get(str(item.get("id", "")), {})
        title = _title(item, analysis)
        source = str(item.get("source", {}).get("name") or "").strip()
        by_country[code].append(f"{title}（{source}）" if source else title)
    if not any(by_country.values()):
        return ""
    blocks: list[str] = []
    for code in ("CN", "US", "JP"):
        entries = by_country[code][:per_country]
        body = "；".join(entries) if entries else "（无最新官方条目）"
        blocks.append(f"[{labels[code]}] {body}")
    return "\n".join(blocks)


def _llm_macro_background(
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    graph_cfg: dict[str, Any],
    config: dict[str, Any],
    hermes_llm: Any | None = None,
    llm_audit: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """Synthesise the 中美日 macro background via one LLM call.

    Returns ``{}`` on empty digest or any failure so the caller falls back to
    the deterministic, title-based macro background.
    """
    digest = _build_official_digest(items, analyses)
    if not digest:
        return {}
    try:
        adapter = _new_research_adapter(
            graph_cfg, hermes_llm, llm_audit if llm_audit is not None else []
        )
        return adapter.macro_background(
            digest, language=str(config.get("output_language", "zh-CN"))
        )
    except Exception:
        logger.exception("macro background synthesis failed")
        return {}


def _derive_mainlines(
    items: list[dict[str, Any]], analyses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build fallback daily mainlines from the top items + analyses.

    Uses the first signal from each analysis as the headline detail,
    producing richer mainlines than bare titles.
    """
    from ...briefing_builder import _is_google_news_item, _region, _topics

    analysis_by_id = {str(a.get("news_item_id", "")): a for a in analyses}
    mainlines: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items or []:
        if _is_google_news_item(item):
            continue
        item_id = str(item.get("id", ""))
        analysis = analysis_by_id.get(item_id, {})
        signals = analysis.get("signals", [])
        if not signals:
            continue
        if isinstance(signals[0], dict):
            signal_text = signals[0].get("signal", "")
        else:
            signal_text = str(signals[0])
        if not signal_text:
            continue

        region = _region(item)
        topics_list = _topics(analysis)
        topic_str = topics_list[0] if topics_list else "宏观经济"
        title = str(analysis.get("translated_title") or item.get("title") or "")

        headline = f"{region}｜{topic_str}：{title} — {signal_text}"
        if len(headline) > 180:
            headline = headline[:177] + "…"

        if headline not in seen:
            seen.add(headline)
            mainlines.append(
                {
                    "headline": headline,
                    "supporting_item_ids": [item_id],
                }
            )
        if len(mainlines) >= 5:
            break

    return mainlines


def _auto_enrich_signals(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    config: dict[str, Any],
    graph_cfg: dict[str, Any],
    hermes_llm: Any,
    llm_audit: list[dict[str, Any]],
) -> None:
    """Fill missing signals inline; updates ``analyses`` and ``state`` in place."""
    # ── Auto-enrich missing signals ─────────────────────────────────────
    # Cover ALL displayed items lacking signals (capped), not just the top 10:
    # items ranked below 10 (e.g. a PBoC release at rank 15) were otherwise
    # left rendering "待基于已核验来源补充" whenever their analyze call returned
    # unparseable JSON. After analyze's own retry few items reach here, so the
    # extra calls are rare in practice; the cap bounds the pathological case.
    graph_cfg_local = config.get("graph", {}) if isinstance(config, dict) else {}
    enrich_cap = int(graph_cfg_local.get("enrich_max_items", 25))
    analysis_by_id = {str(a.get("news_item_id", "")): a for a in analyses}
    updated = False
    for item in (items or [])[:enrich_cap]:
        item_id = str(item.get("id", ""))
        analysis = analysis_by_id.get(item_id, {})
        if analysis.get("signals"):
            continue  # already has signals
        if not item.get("title"):
            continue  # no title to work with
        try:
            adapter = _new_research_adapter(graph_cfg, hermes_llm, llm_audit, temperature=0.2)
            result = adapter.analyze(
                title=str(item.get("title", "")),
                source_context={"title": item.get("title"), "url": item.get("url")},
                evidence=[],  # no evidence available
                relevance_score=float(item.get("relevance_score", 0)),
                language=str(config.get("output_language", "zh-CN")),
            )
            if result.get("signals"):
                # Merge back into the analyses list
                for i, a in enumerate(analyses):
                    if a.get("news_item_id") == item_id:
                        analyses[i] = {**a, **result}
                        updated = True
                        break
                if not updated:
                    analysis["signals"] = result["signals"]
                    analysis["focus_assets"] = result.get("focus_assets", [])
                    analyses.append(result)
                    updated = True
        except Exception:
            logger.exception("auto-enrich failed for item %s", item_id)

    if updated:
        state["analysis"] = analyses
        # Re-derive mainlines now that we have more signals
        mainlines = list(state.get("mainlines", []))
        if not mainlines:
            mainlines = _derive_mainlines(items, analyses)
            state["mainlines"] = mainlines


def _derive_briefing_fallbacks(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    config: dict[str, Any],
    graph_cfg: dict[str, Any],
    hermes_llm: Any,
    llm_audit: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Any]:
    """Return ``(mainlines, market_impact)``, deriving and persisting them on ``state``."""
    # ── Derive the deterministic briefing fallbacks BEFORE the gate ──────
    # ``mainlines`` and ``daily.market_impact`` are produced by
    # ``build_briefing_node``, which runs AFTER this gate. Without deriving
    # them here the gate always sees them empty and can never pass, so the
    # email is never sent. We derive them with the same helpers
    # ``build_briefing_node`` uses and persist them so the briefing reuses
    # the exact values the gate approved.
    mainlines = list(state.get("mainlines", []))
    if not mainlines:
        mainlines = _derive_mainlines(items, analyses)
        state["mainlines"] = mainlines

    daily = state.get("daily", {}) or {}
    market_impact = daily.get("market_impact") if isinstance(daily, dict) else {}
    if not market_impact:
        # Prefer a real LLM-synthesised impact map (one call); fall back to the
        # deterministic placeholder map when there are no signals or it fails.
        market_impact = (
            _llm_market_impact(analyses, graph_cfg, config, hermes_llm, llm_audit)
            or _market_impact_from_items(items, analyses)
            or {}
        )
        state["daily"] = {
            **(daily if isinstance(daily, dict) else {}),
            "market_impact": market_impact,
        }

    return mainlines, market_impact


def _synthesize_macro_background(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    config: dict[str, Any],
    graph_cfg: dict[str, Any],
    hermes_llm: Any,
    llm_audit: list[dict[str, Any]],
) -> tuple[Any, Any]:
    """Return ``(daily, macro_background)``; ``daily`` is the value read before synthesis."""
    # ── 中美日宏观背景：优先 LLM 合成三段叙述 ─────────────────────────────
    # 留空时 build_briefing_node 会回退到 builder 的确定性（标题归并）版本。
    daily = state.get("daily", {}) or {}
    macro_background = daily.get("macro_background") if isinstance(daily, dict) else {}
    if not macro_background:
        macro_background = _llm_macro_background(
            items, analyses, graph_cfg, config, hermes_llm, llm_audit
        )
        if macro_background:
            state["daily"] = {
                **(daily if isinstance(daily, dict) else {}),
                "macro_background": macro_background,
            }

    return daily, macro_background


def _find_untranslated_titles(
    analyses: list[dict[str, Any]],
    items: list[dict[str, Any]],
    important_ids: set[str],
) -> list[str]:
    """Ids of important items whose displayed title is still not zh-CN."""
    # Strict gate: important items whose displayed title is still non-zh-CN
    # (LLM failed to translate). Unverified Google News discovery items are
    # exempt — their原文 is intentionally not treated as verified content.
    from ...briefing_builder import _is_google_news_item, _is_verified_google_item

    item_by_id = {str(i.get("id", "")): i for i in items}
    untranslated = []
    for a in analyses:
        aid = str(a.get("news_item_id", ""))
        if aid not in important_ids:
            continue
        item = item_by_id.get(aid, {})
        if _is_google_news_item(item) and not _is_verified_google_item(item):
            continue
        title = a.get("translated_title") or item.get("translated_title") or item.get("title") or ""
        if _looks_untranslated(title):
            untranslated.append(aid)

    return untranslated


def _evaluate_gate(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    mainlines: list[dict[str, Any]],
    market_impact: Any,
) -> dict[str, Any]:
    """Check the briefing pieces and return the ``_quality_gate`` record."""
    evidence = list(state.get("_evidence", []))

    important_ids = {str(a.get("news_item_id", "")) for a in analyses[:5] if a.get("news_item_id")}
    evidence_by_item: dict[str, list[dict[str, Any]]] = {}
    for entry in evidence:
        evidence_by_item.setdefault(str(entry.get("item_id", "")), []).append(entry)
    # Evidence is best-effort: auto-enrich fills signals from the title alone
    # when no article body could be fetched, so a missing fetch must not block
    # delivery. We still compute it for the warnings/log below.
    missing_evidence = [
        item_id
        for item_id in important_ids
        if not any(e.get("content") for e in evidence_by_item.get(item_id, []))
    ]

    missing_signals = [
        a
        for a in analyses
        if important_ids.intersection({str(a.get("news_item_id", ""))}) and not a.get("signals")
    ]

    untranslated = _find_untranslated_titles(analyses, items, important_ids)

    mainlines_ok = bool(mainlines) and 1 <= len(mainlines) <= 5

    required_market_keys = {"rates_bonds", "fx", "equities", "commodities", "risk_appetite"}
    market_ok = (
        isinstance(market_impact, dict)
        and required_market_keys.issubset(set(market_impact.keys()))
        and all(str(market_impact.get(k, "")).strip() for k in required_market_keys)
    )

    issues: list[str] = []
    if missing_signals:
        issues.append(
            "missing_signals:" + ",".join(str(a.get("news_item_id", "")) for a in missing_signals)
        )
    if untranslated:
        issues.append("untranslated_titles:" + ",".join(untranslated))
    if not mainlines_ok:
        issues.append("mainlines_invalid")
    if not market_ok:
        issues.append("market_impact_map_incomplete")

    if missing_evidence:
        logger.info(
            "quality_gate: %d important item(s) without fetched evidence "
            "(best-effort, not blocking): %s",
            len(missing_evidence),
            ",".join(sorted(missing_evidence)),
        )

    passed = not issues
    gate = {
        "passed": passed,
        "issues": issues,
        "missing_evidence": sorted(missing_evidence),
        "untranslated_titles": sorted(untranslated),
        "checked_at": _now_iso(),
    }

    return gate


def _quality_unbudgeted(state: dict[str, Any]) -> dict[str, Any]:
    """Inspect the briefing for missing pieces and auto-enrich before gate.

    When items lack signals, mainlines, or market_impact, the LLM is
    called inline to fill them — replacing the external enrichment step.
    """
    items = list(state.get("ranked_items") or state.get("items") or [])
    analyses = list(state.get("analysis", []))
    config = state.get("config", {}) or {}
    graph_cfg = config.get("graph", {}) or {}
    hermes_llm = _resolve_hermes_llm(graph_cfg, state)
    llm_audit = state.setdefault("llm_audit", [])

    _auto_enrich_signals(state, items, analyses, config, graph_cfg, hermes_llm, llm_audit)

    mainlines, market_impact = _derive_briefing_fallbacks(
        state, items, analyses, config, graph_cfg, hermes_llm, llm_audit
    )

    daily, macro_background = _synthesize_macro_background(
        state, items, analyses, config, graph_cfg, hermes_llm, llm_audit
    )

    gate = _evaluate_gate(state, items, analyses, mainlines, market_impact)
    passed = gate["passed"]

    # Increment on each failure so ``max_quality_retries`` actually bounds the
    # retry loop. The previous ``max(1, ...)`` capped the counter at 1, so any
    # ``max_quality_retries >= 2`` made the gate retry forever (until LangGraph's
    # recursion limit aborted the run).
    new_retry_count = int(state.get("_retry_count", 0))
    if not passed:
        new_retry_count += 1

    return {
        "_quality_gate": gate,
        "_retry_count": new_retry_count,
        "_phase": "quality_checked",
        # Return (not just mutate) the derived fallbacks so they propagate to
        # build_briefing_node — LangGraph merges returned updates, not in-place
        # state mutations. Without this the LLM market-impact map computed above
        # is lost and build_briefing falls back to the placeholder map.
        "mainlines": mainlines,
        "daily": {
            **(daily if isinstance(daily, dict) else {}),
            "market_impact": market_impact,
            **({"macro_background": macro_background} if macro_background else {}),
        },
    }


def quality_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    update = _budgeted(state, "quality", 1, "quality gate", lambda: _quality_unbudgeted(state))
    if update.get("_run_budget", {}).get("degraded") and "_quality_gate" not in update:
        update["_quality_gate"] = {"passed": False, "issues": ["budget_limited"]}
    return update
