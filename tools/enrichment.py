#!/usr/bin/env python3
"""Apply validated, source-safe structured enrichment to Runtime results."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.briefing_builder import build_daily_briefing, build_daily_briefing_html
from tools.config_loader import ROOT
from tools.email_delivery import build_email_artifact
from tools.schema_validation import load_schema, validate, validate_file


def load_translations(path: Path) -> dict[str, str]:
    """Load a title-only compatibility payload without executing external text."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "translations" in payload:
        payload = payload["translations"]
    if isinstance(payload, dict):
        payload = [{"news_item_id": key, "translated_title": value} for key, value in payload.items()]
    if not isinstance(payload, list):
        raise ValueError("translations must be an object or an array")
    translations: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("each translation entry must be an object")
        item_id = str(entry.get("news_item_id", "")).strip()
        title = str(entry.get("translated_title", "")).strip()
        if not item_id or not title:
            raise ValueError("each translation entry requires news_item_id and translated_title")
        translations[item_id] = title
    return translations


def load_enrichment(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate(payload, load_schema(ROOT / "schemas" / "enrichment_patch.schema.json"), ROOT / "schemas")
    return payload


def apply_enrichment(result: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge only approved fields and rerender the fixed-format daily briefing."""
    validate(patch, load_schema(ROOT / "schemas" / "enrichment_patch.schema.json"), ROOT / "schemas")
    enriched = deepcopy(result)
    items_by_id = {str(item.get("id", "")): item for item in enriched["items"]}
    analyses_by_id = {str(analysis.get("news_item_id", "")): analysis for analysis in enriched["analyses"]}
    updates = patch["analyses"]
    unknown_ids = sorted({str(entry["news_item_id"]) for entry in updates} - set(items_by_id))
    if unknown_ids:
        raise ValueError(f"unknown news_item_id values: {', '.join(unknown_ids)}")
    for entry in updates:
        item_id = str(entry["news_item_id"])
        analysis = analyses_by_id.get(item_id)
        for field in ("translated_title", "summary", "signals", "focus_assets", "topics"):
            if field not in entry:
                continue
            if field == "translated_title":
                items_by_id[item_id][field] = entry[field]
            if analysis is not None:
                analysis[field] = entry[field]
                fields = analysis.setdefault("agent_enriched_fields", [])
                if field not in fields:
                    fields.append(field)
    if "daily" in patch:
        enriched["daily_market_impact"] = patch["daily"]["market_impact"]
        enriched["daily_mainlines"] = patch["daily"]["mainlines"]
        if "weekly_watchlist" in patch["daily"]:
            enriched["daily_weekly_watchlist"] = patch["daily"]["weekly_watchlist"]
    if "briefing_markdown" in enriched:
        report_date = str(enriched.get("started_at") or "")[:10] or None
        enriched["briefing_markdown"] = build_daily_briefing(
            enriched["items"], enriched["analyses"], report_date,
            enriched.get("daily_market_impact"), enriched.get("daily_mainlines"),
            enriched.get("daily_weekly_watchlist"),
        )
    enriched.setdefault("warnings", []).append(f"Applied structured enrichment to {len(updates)} item(s).")
    validate_file(enriched, ROOT / "schemas" / "runtime_result.schema.json")
    return enriched


def apply_translations(result: dict[str, Any], translations: dict[str, str]) -> dict[str, Any]:
    """Compatibility wrapper for legacy title-only payloads."""
    return apply_enrichment(result, {"analyses": [
        {"news_item_id": item_id, "translated_title": title}
        for item_id, title in translations.items()
    ]})


def _is_google_discovery(item: dict[str, Any]) -> bool:
    discovery = item.get("discovery", {})
    return (
        isinstance(discovery, dict) and discovery.get("channel") == "google_news"
    ) or str(item.get("source", {}).get("id", "")).startswith("google_news_")


def validate_email_readiness(result: dict[str, Any]) -> None:
    """Reject MML generation until the Agent has covered report-critical fields."""
    analyses = {
        str(analysis.get("news_item_id", "")): analysis
        for analysis in result.get("analyses", [])
    }
    verified_items = [
        item for item in result.get("items", [])
        if not _is_google_discovery(item)
        or item.get("discovery", {}).get("verification_status") == "verified"
    ]
    missing_signals = [
        str(item.get("id", "")) for item in verified_items
        if not analyses.get(str(item.get("id", "")), {}).get("signals")
    ]
    mainline_candidates = verified_items[:5]
    missing_topics = [
        str(item.get("id", "")) for item in mainline_candidates
        if "topics" not in analyses.get(str(item.get("id", "")), {}).get("agent_enriched_fields", [])
    ]
    daily_mainlines = result.get("daily_mainlines")
    candidate_ids = {str(item.get("id", "")) for item in mainline_candidates}
    invalid_mainline_ids = [
        item_id for line in (daily_mainlines or [])
        for item_id in line.get("supporting_item_ids", [])
        if item_id not in candidate_ids
    ]
    # Check: all unverified Google News items must have a translated_title
    untranslated_google = [
        str(item.get("id", "")) for item in result.get("items", [])
        if _is_google_discovery(item)
        and item.get("discovery", {}).get("verification_status") != "verified"
        and not item.get("translated_title")
        and not analyses.get(str(item.get("id", "")), {}).get("translated_title")
    ]
    if missing_signals or missing_topics or "daily_market_impact" not in result or not daily_mainlines or invalid_mainline_ids or untranslated_google:
        problems: list[str] = []
        if missing_signals:
            problems.append(f"核心信号缺失: {', '.join(missing_signals)}")
        if missing_topics:
            problems.append(f"主线候选主题未确认: {', '.join(missing_topics)}")
        if "daily_market_impact" not in result:
            problems.append("日报市场影响地图缺失")
        if not daily_mainlines:
            problems.append("日报宏观主线缺失")
        if invalid_mainline_ids:
            problems.append(f"日报宏观主线引用了非候选新闻: {', '.join(invalid_mainline_ids)}")
        if untranslated_google:
            problems.append(f"Google News 条目待翻译: {', '.join(untranslated_google)}")
        raise ValueError("MML quality gate failed — " + "；".join(problems))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply restricted enrichment to a Runtime result JSON file")
    parser.add_argument("--input", required=True, type=Path, help="Runtime result JSON")
    parser.add_argument("--translations", type=Path, help="Title-only compatibility JSON")
    parser.add_argument("--enrichment", type=Path, help="Restricted structured analysis patch JSON")
    parser.add_argument("--output", required=True, type=Path, help="Output Runtime result JSON")
    parser.add_argument("--markdown-output", type=Path, help="Optional regenerated briefing Markdown")
    parser.add_argument("--email-config", type=Path, help="Email JSON used to render a post-enrichment MML")
    parser.add_argument("--mml-output", type=Path, help="Output MML path; requires --markdown-output and --email-config")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not args.translations and not args.enrichment:
            raise ValueError("provide --translations or --enrichment")
        result = json.loads(args.input.read_text(encoding="utf-8"))
        validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")
        enriched = result
        if args.translations:
            enriched = apply_translations(enriched, load_translations(args.translations))
        if args.enrichment:
            enriched = apply_enrichment(enriched, load_enrichment(args.enrichment))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.markdown_output:
            markdown = enriched.get("briefing_markdown")
            if markdown is None:
                raise ValueError("input Runtime result has no briefing_markdown")
            args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_output.write_text(markdown, encoding="utf-8")
        if args.mml_output:
            if not args.markdown_output or not args.email_config:
                raise ValueError("--mml-output requires --markdown-output and --email-config")
            validate_email_readiness(enriched)
            email_config = json.loads(args.email_config.read_text(encoding="utf-8"))
            if not isinstance(email_config, dict):
                raise ValueError("email config must be a JSON object")
            report_date = str(enriched.get("started_at") or "")[:10] or None
            html_body = build_daily_briefing_html(
                enriched["items"], enriched["analyses"], report_date,
                enriched["daily_market_impact"], enriched["daily_mainlines"],
                enriched.get("daily_weekly_watchlist"),
            )
            version = enriched.get("skill", {}).get("version", "")
            build_email_artifact(args.markdown_output, email_config, output_path=args.mml_output, html_body=html_body, version=version)
    except Exception as exc:
        print(f"enrichment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "success", "output": str(args.output), "markdown_output": str(args.markdown_output) if args.markdown_output else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
