"""Graph nodes for the economic news analysis workflow.

Each node is a pure function that takes the current ``NewsAnalysisState``
and returns a partial update. The LangGraph framework merges the update
into the running state. Errors that bubble out of a node are caught and
recorded on ``state['errors']`` so a single bad item never blocks the
rest of the pipeline.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..analyzer import analyses_to_markdown, analyze_item, analyze_items
from ..briefing_builder import SEPARATOR, build_daily_briefing, build_daily_briefing_html
from ..config_loader import ROOT
from ..delivery_trace import effective_delivery_config
from ..email_delivery import send_email
from ..email_render import build_combined_email_html, md_to_html, md_to_plain
from ..evidence_fetcher import batch_fetch, fetch_evidence
from ..feed_fetcher import fetch_from_config, source_inventory
from ..google_news_enrichment import enrich_google_news_items
from ..markdown_writer import write_markdown_artifact
from ..market_data import build_market_context
from ..movers import fetch_mover_items, movers_enabled
from ..normalizer import normalize_news_items
from ..relevance_ranker import balance_by_country, rank_items
from ..run_log import append_log, evaluate_fetch_window, read_last_entry
from ..trend_table import build_trend_rows, render_html, render_markdown
from .events import record_event
from .llm_runtime import get_run_hermes_llm
from .research import ResearchAdapter

logger = logging.getLogger(__name__)


_AGGREGATOR_HOST_HINTS = (
    "news.google.com",
    "google_news",
    "rssaggregator",
    "feedburner",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_errors(state: dict[str, Any], new_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge new error dicts onto ``state['errors']`` without losing earlier entries."""
    merged: list[dict[str, Any]] = list(state.get("errors", []))
    merged.extend(new_errors)
    return merged


def _has_valid_markdown(value: Any) -> bool:
    """Return true when markdown contains content beyond headings/placeholders."""
    if not isinstance(value, str):
        return False
    for line in value.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped or re.fullmatch(r"#{1,6}\s+.*", stripped):
            continue
        if stripped in {"待补充", "暂无内容", "N/A", "--"}:
            continue
        if re.search(r"由\s*Economic News Analysis\b.*自动生成", stripped, re.IGNORECASE):
            continue
        if re.match(r"数据来源\s*[:：]", stripped):
            continue
        if re.search(r"[\w\u3400-\u9fff]", stripped):
            return True
    return False


def _has_valid_html(value: Any) -> bool:
    """Return true when HTML has substantive non-heading text."""
    if not isinstance(value, str):
        return False
    without_headings = re.sub(
        r"<h[1-6]\b[^>]*>.*?</h[1-6]>", " ", value, flags=re.IGNORECASE | re.DOTALL
    )
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", without_headings))
    text = re.sub(
        r"由\s*Economic News Analysis\b.*?自动生成",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"数据来源\s*[:：].*", " ", text, flags=re.IGNORECASE | re.DOTALL)
    return bool(re.search(r"[\w\u3400-\u9fff]", text))


def _html_to_plain(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _valid_attachment(path_value: Any) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        if path.suffix.lower() in {".md", ".markdown"}:
            if not _has_valid_markdown(path.read_text(encoding="utf-8-sig")):
                return None
    except (OSError, UnicodeError):
        return None
    return path


def _pipeline_status(state: dict[str, Any], *, has_content: bool) -> str:
    """Compute business-pipeline status without consulting output or delivery."""
    if not has_content:
        return "failed"
    gate = state.get("_quality_gate") or {}
    has_pipeline_issues = bool(
        state.get("errors")
        or int(state.get("_sources_failed", 0)) > 0
        or (gate and not gate.get("passed", False))
    )
    return "partial_success" if has_pipeline_issues else "success"


def _missing_sources(state: dict[str, Any]) -> list[str]:
    """Collect stable source labels for a partial-success report."""
    found: list[str] = []
    for entry in list(state.get("skipped_sources", [])) + list(state.get("errors", [])):
        if not isinstance(entry, dict):
            continue
        label = (
            entry.get("source_name")
            or entry.get("source_id")
            or entry.get("source")
            or entry.get("url")
        )
        if label and str(label) not in found:
            found.append(str(label))
    failed_count = int(state.get("_sources_failed", 0))
    if failed_count and not found:
        found.append(f"{failed_count} 个未标识来源")
    return found


def _partial_report_notice(state: dict[str, Any]) -> tuple[str, str, str]:
    missing = _missing_sources(state)
    details = "、".join(missing) if missing else "存在非致命错误，详见 warnings"
    warning = f"部分成功：缺失或跳过来源：{details}"
    markdown = f"> **部分成功**：本报告基于当前可用内容生成。\n> 缺失或跳过来源：{details}"
    html = (
        '<div style="padding:10px;border-left:4px solid #d97706;background:#fffbeb;">'
        f"<strong>部分成功</strong>：本报告基于当前可用内容生成。<br>"
        f"缺失或跳过来源：{details}</div>"
    )
    return markdown, html, warning


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


def fetch_feeds_node(state: dict[str, Any]) -> dict[str, Any]:
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


def rank_items_node(state: dict[str, Any]) -> dict[str, Any]:
    """Rank items by macroeconomic relevance with per-category quotas."""
    config = state.get("config", {}) or {}
    items = list(state.get("items", []))
    if not items:
        return {"ranked_items": [], "items": [], "_phase": "ranked"}
    try:
        from ..briefing_builder import _item_category

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


def _url_priority(url: str) -> int:
    lowered = (url or "").lower()
    if not lowered:
        return 3
    for hint in _AGGREGATOR_HOST_HINTS:
        if hint in lowered:
            return 2
    return 0


def collect_evidence_node(
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


def _resolve_hermes_llm(graph_cfg: dict[str, Any], state: dict[str, Any]) -> Any | None:
    """Resolve the non-serializable Hermes facade for this graph run."""
    return graph_cfg.get("_hermes_llm") or get_run_hermes_llm(state.get("run_id"))


def _new_research_adapter(
    graph_cfg: dict[str, Any],
    hermes_llm: Any | None,
    llm_audit: list[dict[str, str]],
    temperature: float | None = None,
) -> ResearchAdapter:
    """Create a graph adapter without touching provider SDKs or credentials."""
    adapter = ResearchAdapter(
        temperature=float(
            graph_cfg.get("temperature", 0.2) if temperature is None else temperature
        ),
        hermes_llm=hermes_llm,
        model_routes=dict(graph_cfg.get("model_routes") or {}),
        strict_model_routes=bool(graph_cfg.get("strict_model_routes", True)),
        llm_audit=llm_audit,
    )
    fake_llm = graph_cfg.get("_fake_llm")
    if fake_llm is not None:
        adapter.set_fake_llm(fake_llm)
    return adapter


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


def analyze_with_llm_node(state: dict[str, Any]) -> dict[str, Any]:
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
    from ..briefing_builder import _item_category, _title

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
    from ..briefing_builder import _is_google_news_item, _region, _topics

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


def quality_gate_node(state: dict[str, Any]) -> dict[str, Any]:
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
            daily = state.get("daily", {}) or {}
            mainlines = _derive_mainlines(items, analyses)
            state["mainlines"] = mainlines

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

    # ── Gate check ────────────────────────────────────────────────────
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

    # Strict gate: important items whose displayed title is still non-zh-CN
    # (LLM failed to translate). Unverified Google News discovery items are
    # exempt — their原文 is intentionally not treated as verified content.
    from ..briefing_builder import _is_google_news_item, _is_verified_google_item

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


def build_briefing_node(state: dict[str, Any]) -> dict[str, Any]:
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


def deliver_email_node(state: dict[str, Any]) -> dict[str, Any]:
    """Select and execute attachment, body-only, or failure-notice delivery."""
    config = state.get("config", {}) or {}
    warnings: list[str] = list(state.get("warnings", []))
    output_files = dict(state.get("output_files", {}))
    delivery = dict(state.get("delivery", {}))
    mode = state.get("mode", "")
    pipeline_status = str(state.get("pipeline_status", "running"))
    output_status = str(state.get("output_status", "skipped"))
    effective_config = effective_delivery_config(config)
    delivery_requested = bool(effective_config["deliver_email"])

    def _trace_fields(
        *, email_sent: bool, email_send_attempted: bool, delivery_skip_reason: str | None
    ) -> dict[str, Any]:
        fields = {
            "delivery_requested": delivery_requested,
            "email_send_attempted": email_send_attempted,
            "email_sent": email_sent,
            "delivery_skip_reason": delivery_skip_reason,
            "effective_delivery_config": effective_config,
        }
        logger.info("delivery trace: %s", fields)
        return fields

    if delivery.get("email_sent"):
        run_id = state.get("run_id", "")
        warnings.append(f"run_id {run_id} 邮件已发送，跳过重复投递。")
        return {
            "warnings": warnings,
            "output_files": output_files,
            "delivery": delivery,
            **_trace_fields(
                email_sent=True, email_send_attempted=False, delivery_skip_reason="already_sent"
            ),
            "email_sent": True,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "success",
            "delivery_kind": delivery.get("delivery_kind", state.get("delivery_kind", "none")),
            "_phase": "delivered",
        }

    if not config.get("deliver_email", False):
        warnings.append("deliver_email 未启用，跳过邮件投递。")
        return {
            "warnings": warnings,
            "output_files": output_files,
            **_trace_fields(
                email_sent=False,
                email_send_attempted=False,
                delivery_skip_reason="delivery_not_requested",
            ),
            "delivery": delivery,
            "email_sent": False,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "_phase": "delivered",
        }

    skill_version = config.get("_skill", {}).get("version", "")
    email_cfg = dict(config.get("email", {}) or {})
    if not email_cfg.get("send", False):
        warnings.append("email.send 未启用，跳过邮件投递。")
        return {
            "warnings": warnings,
            **_trace_fields(
                email_sent=False,
                email_send_attempted=False,
                delivery_skip_reason="email_send_disabled",
            ),
            "output_files": output_files,
            "delivery": delivery,
            "email_sent": False,
            "pipeline_status": pipeline_status,
            "output_status": output_status,
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "_phase": "delivered",
        }

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    jst = now.astimezone(timezone(timedelta(hours=9)))
    date_str = jst.strftime("%Y-%m-%d")
    subject = f"📊 财经消息 日报 {date_str}"

    briefing = state.get("briefing") or {}
    trend_table = state.get("trend_table") or {}
    trend_markdown = trend_table.get("markdown") or state.get("trend_markdown") or ""
    briefing_markdown = (
        state.get("briefing_markdown")
        or (briefing.get("markdown") if isinstance(briefing, dict) else "")
        or trend_markdown
        or ""
    )
    briefing_html = briefing.get("html") if isinstance(briefing, dict) else ""
    has_markdown = _has_valid_markdown(briefing_markdown)
    has_html = _has_valid_html(briefing_html)
    has_content = has_markdown or has_html
    pipeline_status = _pipeline_status(state, has_content=has_content)
    if not has_content:
        output_status = "skipped"

    trend_rows = list(trend_table.get("rows", []))
    items = list(state.get("ranked_items") or state.get("items") or [])
    has_new_items = bool(items)
    if has_new_items and mode != "trend":
        log_path = config.get("log_path")
        if log_path:
            last_entry = read_last_entry(Path(log_path))
            if last_entry and last_entry.get("item_urls"):
                prev_urls = set(last_entry.get("item_urls", []))
                cur_urls = {i.get("url") or i.get("link") or i.get("id", "") for i in items}
                cur_urls.discard("")
                if cur_urls and cur_urls <= prev_urls:
                    has_new_items = False
                    warnings.append("所有新闻条目已在上一轮发送，跳过简报区。")

    attachment = _valid_attachment(output_files.get("markdown"))
    if has_content:
        kind = "report_with_attachment" if attachment else "report_without_attachment"
        text_body = (
            md_to_plain(str(briefing_markdown))
            if has_markdown
            else _html_to_plain(str(briefing_html))
        )
        try:
            combined_html = build_combined_email_html(
                trend_rows=trend_rows,
                briefing_markdown=str(briefing_markdown) if has_markdown else None,
                briefing_html=str(briefing_html) if has_html else None,
                as_of=trend_table.get("as_of"),
                has_news_items=has_new_items and mode != "trend",
                version=skill_version,
                report_date=date_str,
            )
        except Exception as exc:
            logger.exception("build_combined_email_html failed")
            combined_html = str(briefing_html) if has_html else md_to_html(str(briefing_markdown))
            warnings.append(f"组合邮件 HTML 构建失败，已降级: {exc}")
    else:
        kind = "failure_notification"
        subject = f"[失败通知] 财经消息日报 {date_str}"
        reasons = [str(error.get("message") or error) for error in state.get("errors", [])]
        reason_text = "；".join(reasons) or "流水线未生成任何有效业务正文。"
        text_body = f"财经消息日报生成失败。原因：{reason_text}"
        combined_html = f"<p>财经消息日报生成失败。</p><p>原因：{reason_text}</p>"

    result = send_email(
        email_config=email_cfg,
        subject=subject,
        text_body=text_body,
        html_body=combined_html,
        attachments=(attachment,) if attachment else (),
    )
    warnings.extend(result.warnings)
    sent = result.sent
    if not sent:
        warnings.append(f"邮件发送失败: {result.error or '未知错误'}")
    trace_fields = _trace_fields(
        email_sent=sent, email_send_attempted=True, delivery_skip_reason=None
    )
    delivery.update(
        {
            "email_sent": sent,
            "delivery_status": "success" if sent else "failed",
            "delivery_kind": kind,
            **trace_fields,
        }
    )
    if sent:
        delivery["email_sent_at"] = _now_iso()

    return {
        "warnings": warnings,
        **trace_fields,
        "output_files": output_files,
        "delivery": delivery,
        "email_sent": sent,
        "pipeline_status": pipeline_status,
        "output_status": output_status,
        "delivery_status": "success" if sent else "failed",
        "delivery_kind": kind,
        "_phase": "delivered",
    }


def write_run_log_node(state: dict[str, Any]) -> dict[str, Any]:
    """Append a JSONL log entry when ``config['log_path']`` is configured."""
    config = state.get("config", {}) or {}
    log_path = config.get("log_path")
    if not log_path:
        return {"_phase": "logged"}
    items = list(state.get("ranked_items") or state.get("items") or [])
    entry = {
        "run_id": state.get("run_id", ""),
        "mode": state.get("mode", ""),
        "status": state.get("pipeline_status", "success"),
        "pipeline_status": state.get("pipeline_status", "success"),
        "output_status": state.get("output_status", "skipped"),
        "delivery_status": state.get("delivery_status", "skipped"),
        "delivery_kind": state.get("delivery_kind", "none"),
        "email_sent": bool(state.get("email_sent", False)),
        "delivery_requested": bool(state.get("delivery_requested", False)),
        "email_send_attempted": bool(state.get("email_send_attempted", False)),
        "delivery_skip_reason": state.get("delivery_skip_reason"),
        "effective_delivery_config": effective_delivery_config(config),
        "started_at": state.get("started_at", ""),
        "completed_at": state.get("completed_at") or _now_iso(),
        "fetched_at": state.get("started_at", ""),
        "sources_successful": state.get("_sources_successful", 0),
        "sources_failed": state.get("_sources_failed", 0),
        "sources_skipped": state.get("_sources_skipped", 0),
        "items_fetched": state.get("_items_fetched", 0),
        "items_normalized": len(state.get("items", [])),
        "items_ranked": len(items),
        "items_analyzed": len(state.get("analysis", [])),
        "item_urls": [
            i.get("url") or i.get("link") or i.get("id", "")
            for i in items
            if i.get("url") or i.get("link") or i.get("id")
        ],
    }
    append_log(Path(log_path), entry)
    return {"_run_log_path": str(log_path), "_phase": "logged"}


def check_log_node(state: dict[str, Any]) -> dict[str, Any]:
    """Inspect the runtime log to decide whether to fetch today."""
    config = state.get("config", {}) or {}
    log_path = Path(config.get("log_path") or ROOT / "logs" / "runtime.jsonl")
    check = evaluate_fetch_window(log_path)
    return {"check": check, "_phase": "checked"}


def list_sources_node(state: dict[str, Any]) -> dict[str, Any]:
    """List available sources without performing any network calls."""
    sources = source_inventory(state.get("config", {}) or {})
    return {"sources": sources, "_phase": "listed"}


# Re-export helpers so workflow.py can import them through one symbol.
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
    "quality_gate_node",
    "rank_items_node",
    "trend_table_node",
    "write_outputs_node",
    "write_run_log_node",
    "fetch_evidence",
    "analyze_items",
]

# Budget wrappers are defined after the original nodes so normal graph imports
# receive deadline-aware behavior without changing the underlying adapters.
from .run_budget import (
    begin_stage as _budget_begin,
    complete_stage as _budget_complete,
    should_stop_optional_work as _budget_stop,
)

_fetch_feeds_unbudgeted = fetch_feeds_node
_collect_evidence_unbudgeted = collect_evidence_node
_analyze_unbudgeted = analyze_with_llm_node
_quality_unbudgeted = quality_gate_node
_build_briefing_unbudgeted = build_briefing_node


def _budgeted(state: dict[str, Any], stage: str, target: int, active: str | None, callback):
    budget = state.get("_run_budget")
    if not isinstance(budget, dict):
        return callback()
    _budget_begin(budget, stage, completed_count=0, target_count=target, active_item=active)
    if _budget_stop(budget):
        _budget_complete(budget, stage, completed_count=0, status="skipped")
        return {
            "_run_budget": budget,
            "warnings": list(state.get("warnings", []))
            + [f"Budget downgrade: {stage} stopped at {active or 'stage start'}."],
        }
    update = callback()
    completed = len(update.get("analysis", update.get("_evidence", update.get("items", []))))
    _budget_complete(budget, stage, completed_count=completed)
    return {**update, "_run_budget": budget}


def fetch_feeds_node(state: dict[str, Any]) -> dict[str, Any]:
    return _budgeted(state, "rss", 1, "RSS feeds", lambda: _fetch_feeds_unbudgeted(state))


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


def analyze_with_llm_node(state: dict[str, Any]) -> dict[str, Any]:
    target = len(state.get("ranked_items") or state.get("items") or [])
    return _budgeted(
        state, "analysis", target, "next analysis item", lambda: _analyze_unbudgeted(state)
    )


def quality_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    update = _budgeted(state, "quality", 1, "quality gate", lambda: _quality_unbudgeted(state))
    if update.get("_run_budget", {}).get("degraded") and "_quality_gate" not in update:
        update["_quality_gate"] = {"passed": False, "issues": ["budget_limited"]}
    return update


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


_write_run_log_unbudgeted = write_run_log_node


def write_run_log_node(state: dict[str, Any]) -> dict[str, Any]:
    """Persist a separate stable checkpoint summary beside the legacy log entry."""
    update = _write_run_log_unbudgeted(state)
    budget = state.get("_run_budget")
    log_path = (state.get("config") or {}).get("log_path")
    if isinstance(budget, dict) and log_path:
        append_log(
            Path(log_path),
            {
                "record_type": "stage_checkpoint_summary",
                "run_id": state.get("run_id", ""),
                "budget": budget,
                "completed_at": _now_iso(),
            },
        )
    return {**update, **({"_run_budget": budget} if isinstance(budget, dict) else {})}
