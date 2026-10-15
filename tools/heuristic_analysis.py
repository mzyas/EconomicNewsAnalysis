#!/usr/bin/env python3
"""Heuristic fallback implementation for economic news analysis."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOPIC_KEYWORDS = {
    "monetary_policy": ["fed", "fomc", "boj", "ecb", "central bank", "rate", "interest", "央行", "美联储", "日银", "日本央行", "欧洲央行", "利率", "降息", "加息"],
    "inflation": ["cpi", "ppi", "pce", "inflation", "price", "通胀", "物价", "核心通胀"],
    "employment": ["nfp", "payroll", "unemployment", "wage", "jobless", "就业", "失业", "工资", "薪资"],
    "gdp_growth": ["gdp", "growth", "recession", "pmi", "retail sales", "增长", "衰退", "采购经理", "零售"],
    "fiscal_policy": ["fiscal", "budget", "treasury", "debt", "财政", "预算", "国债"],
    "trade": ["tariff", "export", "import", "trade", "关税", "出口", "进口", "贸易"],
    "fx": ["fx", "currency", "usd", "jpy", "cny", "yen", "dollar", "汇率", "美元", "日元", "人民币"],
    "energy": ["oil", "gas", "energy", "opec", "原油", "能源", "天然气"],
    "geopolitics": ["war", "sanction", "conflict", "geopolitical", "战争", "制裁", "冲突", "地缘"],
}

REGION_KEYWORDS = {
    "US": ["us", "u.s.", "america", "fed", "fomc", "美国", "美联储"],
    "Japan": ["japan", "boj", "yen", "jpy", "日本", "日银", "日本央行", "日元"],
    "China": ["china", "pboc", "cny", "中国", "央行", "人民币", "国家统计局"],
    "Eurozone": ["euro", "ecb", "eurozone", "欧洲", "欧元区", "欧洲央行"],
}

ASSET_KEYWORDS = {
    "USD": ["usd", "dollar", "美元"],
    "JPY": ["jpy", "yen", "日元"],
    "CNY": ["cny", "yuan", "人民币"],
    "Gold": ["gold", "黄金"],
    "Oil": ["oil", "crude", "原油"],
    "Bonds": ["bond", "treasury", "yield", "债券", "美债", "收益率"],
    "Equities": ["stock", "equity", "s&p", "nasdaq", "nikkei", "股市", "股票", "日经"],
}

REGION_LABELS_ZH = {
    "US": "美国",
    "Japan": "日本",
    "China": "中国",
    "Eurozone": "欧元区",
    "global": "全球",
}

TOPIC_LABELS_ZH = {
    "monetary_policy": "货币政策",
    "inflation": "通胀",
    "employment": "就业",
    "gdp_growth": "经济增长",
    "fiscal_policy": "财政政策",
    "trade": "贸易",
    "fx": "汇率",
    "energy": "能源",
    "geopolitics": "地缘政治",
    "macro_news": "宏观经济",
}

ASSET_SECTION_LABELS_ZH = {
    "fx": "汇率",
    "bonds": "债券",
    "equities": "股票",
    "commodities": "商品",
}


def detect(text: str, mapping: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    return [key for key, words in mapping.items() if any(word.lower() in lowered for word in words)]


def summarize(
    text: str,
    topics: list[str],
    regions: list[str],
    source_language: str = "",
) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    is_japanese = source_language.lower().startswith("ja") or bool(
        re.search(r"[\u3040-\u30ff]", compact)
    )
    if re.search(r"[\u3400-\u9fff]", compact) and not is_japanese:
        return compact if len(compact) <= 180 else compact[:180].rstrip() + "..."

    region_text = "、".join(REGION_LABELS_ZH.get(item, item) for item in regions)
    topic_text = "、".join(TOPIC_LABELS_ZH.get(item, item) for item in topics)
    return f"该新闻涉及{region_text}的{topic_text}，具体事实与政策含义需结合原文数据和措辞进一步确认。"


def build_analysis(
    text: str,
    focus: list[str],
    source_language: str = "",
) -> dict:
    topics = detect(text, TOPIC_KEYWORDS) or ["macro_news"]
    regions = detect(text, REGION_KEYWORDS) or ["global"]
    # `focus` controls ranking attention only. It must not be copied into every
    # news row as if every asset were directly affected.
    assets = detect(text, ASSET_KEYWORDS)

    return {
        "output_language": "zh-CN",
        "summary": summarize(text, topics, regions, source_language),
        "regions": regions,
        "topics": topics,
        "focus_assets": assets,
        "facts": [],
        "signals": [],
        "impact_path": [],
        "asset_impact": {
            "fx": "",
            "bonds": "",
            "equities": "",
            "commodities": "",
        },
        "time_horizon": {
            "short_term": "",
            "medium_term": "",
            "long_term": "",
        },
        "watchlist": [],
        "risks": ["来源摘要可能省略上下文", "市场可能已提前定价", "后续数据可能反转当前解读"],
        "confidence": "low",
        "draft_limitations": [
            "Runtime 仅完成来源传递、分类和排序。",
            "事实、经济信号、影响路径和资产影响必须由 agent 基于 source_context 补充。",
            "不得使用 source_context 之外的近期叙事补全缺失事实。",
        ],
        "disclaimer": "这不是投资建议，仅用于新闻理解和宏观分析。",
    }


def to_markdown(result: dict) -> str:
    regions = [
        REGION_LABELS_ZH.get(item, item)
        for item in result["regions"]
    ]
    topics = [
        TOPIC_LABELS_ZH.get(item, item)
        for item in result["topics"]
    ]
    lines = [
        "## 新闻摘要",
        result["summary"],
        "",
        "## 分类",
        f"- 地区：{'、'.join(regions)}",
        f"- 主题：{'、'.join(topics)}",
        f"- 关注资产：{', '.join(result['focus_assets']) if result['focus_assets'] else '未指定'}",
        "",
        "## 核心经济信号",
    ]
    lines.extend(f"- {item}" for item in result["signals"])
    lines += ["", "## 影响路径"]
    lines.extend(f"- {item}" for item in result["impact_path"])
    lines += ["", "## 资产影响"]
    for key, value in result["asset_impact"].items():
        lines.append(
            f"- {ASSET_SECTION_LABELS_ZH.get(key, key)}：{value or '待基于来源上下文分析'}"
        )
    lines += ["", "## 后续观察"]
    lines.extend(f"- {item}" for item in result["watchlist"])
    lines += ["", "## 风险"]
    lines.extend(f"- {item}" for item in result["risks"])
    lines += ["", result["disclaimer"]]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Path to a UTF-8 text file containing the news item.")
    parser.add_argument("--stdin", action="store_true", help="Read news text from stdin.")
    parser.add_argument("--focus", nargs="*", default=[], help="Assets to focus on, such as USDJPY Gold Nikkei.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.input:
        text = args.input.read_text(encoding="utf-8")
    else:
        parser.error("Provide --input or --stdin")

    result = build_analysis(text, args.focus)
    print(to_markdown(result) if args.format == "markdown" else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
