"""Offline heuristic analysis adapter.

The Runtime produces a structured draft. A capable agent should enrich the draft
with source-grounded macro reasoning before presenting it as a final report.
"""

from __future__ import annotations

from typing import Any

from .heuristic_analysis import build_analysis, to_markdown
from .source_quality import assess_grounding_status


def analyze_item(
    item: dict[str, Any],
    focus_assets: list[str] | None = None,
    output_language: str = "zh-CN",
) -> dict[str, Any]:
    text = "\n".join(
        part for part in [item.get("title", ""), item.get("summary", "")] if part
    )
    analysis = build_analysis(
        text,
        focus_assets or [],
        str(item.get("source", {}).get("language", "")),
    )
    analysis["output_language"] = output_language
    analysis["news_item_id"] = item.get("id", "")
    analysis["source_url"] = item.get("url", "")
    analysis["analysis_method"] = "heuristic_draft"
    analysis["requires_agent_enrichment"] = True
    analysis["grounding_status"] = assess_grounding_status(
        str(item.get("title", "")),
        str(item.get("summary", "")),
    )
    if analysis["grounding_status"] == "insufficient_source_context":
        analysis["draft_limitations"].append(
            "来源摘要仅含活动、地点或其他非实质信息；需打开原文核验，否则跳过该条目。"
        )
    analysis["source_context"] = {
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "url": item.get("url", ""),
        "published_at": item.get("published_at", ""),
        "source_name": item.get("source", {}).get("name", ""),
        "source_country": item.get("source", {}).get("country", ""),
        "source_language": item.get("source", {}).get("language", ""),
    }
    return analysis


def analyze_items(
    items: list[dict[str, Any]],
    focus_assets: list[str] | None = None,
    output_language: str = "zh-CN",
) -> list[dict[str, Any]]:
    return [
        analyze_item(item, focus_assets, output_language)
        for item in items
    ]


def analyses_to_markdown(analyses: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for index, analysis in enumerate(analyses, 1):
        if len(analyses) > 1:
            sections.append(f"# 新闻分析 {index}")
        sections.append(to_markdown(analysis))
    return "\n\n*********************\n\n".join(sections)
