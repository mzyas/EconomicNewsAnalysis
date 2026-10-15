"""Build deterministic Simplified Chinese daily briefing models and renderers."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from html import escape
from typing import Any

from .email_render import _importance_label, _truncate_signal
from .relevance_ranker import _parse_dt


def _freshness_label(published_at: Any, now: datetime | None = None) -> str:
    """Short zh-CN recency label for a news item ('' when date is unknown)."""
    dt = _parse_dt(published_at)
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    age = (now - dt).days
    if age <= 0:
        return "今天"
    if age == 1:
        return "昨天"
    if age < 7:
        return f"{age}天前"
    if age < 30:
        return f"{age // 7}周前"
    if age < 365:
        return f"{age // 30}个月前"
    return dt.strftime("%Y-%m-%d")


SEPARATOR = "*********************"
SUMMARY_LIMIT = 180
REGION_LABELS = {
    "US": "美国", "JP": "日本", "CN": "中国", "Eurozone": "欧元区",
    "Global": "全球", "Japan": "日本", "China": "中国", "global": "全球",
    "UK": "英国", "United Kingdom": "英国", "EU": "欧盟",
}
TOPIC_LABELS = {
    "monetary_policy": "货币政策", "inflation": "通胀", "employment": "就业",
    "gdp_growth": "经济增长", "fiscal_policy": "财政政策", "trade": "贸易",
    "fx": "汇率", "energy": "能源", "geopolitics": "地缘政治",
    "macro_news": "宏观经济",
}


def _importance(score: float) -> str:
    return "★★★" if score >= 0.7 else "★★" if score >= 0.4 else "★"


def _table_text(value: Any) -> str:
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _truncate(value: str) -> str:
    return value if len(value) <= SUMMARY_LIMIT else value[:SUMMARY_LIMIT].rstrip() + "…"


def _is_google_news_item(item: dict[str, Any]) -> bool:
    discovery = item.get("discovery", {})
    return (
        isinstance(discovery, dict) and discovery.get("channel") == "google_news"
    ) or str(item.get("source", {}).get("id", "")).startswith("google_news_")


def _item_category(item: dict[str, Any]) -> str:
    """Classify an item into a briefing section.

    Returns one of ``\"official\"`` (央行/政府/国际组织), ``\"media\"`` (财经媒体),
    ``\"tech\"`` (科技与AI), ``\"hot\"`` (综合热点), ``\"google\"`` (Google News), or
    ``\"movers\"`` (行情异动触发的个股/指数新闻).
    Classification is by source ``category`` — tech_media/tech_search → tech,
    hot_search → hot — checked before the generic Google-News fallback so that
    tech/hot keyword feeds land in their dedicated boards.
    """
    cat = str(item.get("source", {}).get("category", ""))
    if cat == "market_mover":
        return "movers"
    if cat in ("tech_media", "tech_search"):
        return "tech"
    if cat == "hot_search":
        return "hot"
    if _is_google_news_item(item):
        return "google"
    if cat == "news_media":
        return "media"
    return "official"


# 热点板块只收「财经/科技/AI 以外」的高热条目，因此把命中财经或科技关键词的
# Top Stories 条目从热点板块剔除（它们若相关会经各自来源进入对应板块）。
_HOT_EXCLUDE_PATTERNS = (
    r"(?i)\b(?:stocks?|markets?|inflation|cpi|ppi|gdp|fed|interest rate|rate cut|"
    r"rate hike|bond|yield|earnings|nasdaq|s&p|dow|forex|currency|dollar|economy|"
    r"economic|tariff|trade war|central bank|recession)\b",
    r"(?i)\b(?:ai|artificial intelligence|chips?|semiconductor|nvidia|openai|llm|"
    r"robot|software|iphone|android|startup|crypto|bitcoin|gpu)\b",
    r"(?:股市|大盘|通胀|利率|降息|加息|美联储|央行|债券|收益率|财报|经济|关税|"
    r"贸易战|汇率|人民币|衰退)",
    r"(?:人工智能|大模型|芯片|半导体|英伟达|科技股|算法|机器人|创业公司)",
)


def _is_finance_or_tech(item: dict[str, Any]) -> bool:
    """True when a (hot) item is clearly finance/tech/AI and should be excluded."""
    blob = " ".join([
        str(item.get("title", "")),
        str(item.get("summary", "")),
        " ".join(str(tag) for tag in item.get("tags", [])),
    ])
    return any(re.search(pattern, blob) for pattern in _HOT_EXCLUDE_PATTERNS)


def _is_verified_google_item(item: dict[str, Any]) -> bool:
    return _is_google_news_item(item) and item.get("discovery", {}).get("verification_status") == "verified"


def _title(item: dict[str, Any], analysis: dict[str, Any]) -> str:
    translated = analysis.get("translated_title") or item.get("translated_title")
    if _is_google_news_item(item) and not translated and not _is_verified_google_item(item):
        return f"待 Agent 忠实翻译：{_table_text(item.get('title'))}"
    return _table_text(translated or item.get("title"))


def _region(item: dict[str, Any]) -> str:
    code = str(item.get("source", {}).get("country") or "Global")
    return REGION_LABELS.get(code, code)


def _topics(analysis: dict[str, Any]) -> list[str]:
    raw = analysis.get("topics", []) or ["macro_news"]
    return [TOPIC_LABELS.get(str(topic), str(topic)) for topic in raw]


def _signal_text(signal: Any) -> str:
    """Extract the display text from a signal entry.

    Signals may be plain strings or structured dicts shaped like
    ``{"signal": "...", "type": "...", "impact": "...", "assets": [...]}``.
    For dicts we keep only the human-readable ``signal`` field so the
    briefing never renders a raw ``str(dict)``.
    """
    if isinstance(signal, dict):
        return _table_text(signal.get("signal", ""))
    return _table_text(signal)


def _row(item: dict[str, Any], analysis: dict[str, Any]) -> dict[str, str]:
    unverified = _is_google_news_item(item) and not _is_verified_google_item(item)
    title = _title(item, analysis)
    source_name = _table_text(item.get("source", {}).get("name")) or "未知来源"
    if unverified:
        detail = "原文未核验；仅保留 Google News 发现链接。"
        signal = "来源不足，未提供核心信号。"
        assets = "来源不足"
    else:
        # Prefer the LLM's zh-CN translation/summary; fall back to the raw
        # (often English) source summary only when no translation exists.
        detail = (
            _truncate(_table_text(analysis.get("translated_summary")))
            or _truncate(_table_text(item.get("summary")))
            or "来源未提供摘要。"
        )
        raw_signals = analysis.get("signals", [])
        if raw_signals:
            texts = [_signal_text(s) for s in raw_signals]
            signal = "；".join(t for t in texts if t) or "待基于已核验来源补充。"
        else:
            signal = "待基于已核验来源补充。"
        assets = "、".join(_table_text(value) for value in analysis.get("focus_assets", []) if value) or "综合市场"
    return {
        "importance": _importance(float(item.get("relevance_score", 0))),
        "region": _region(item),
        "topic": "、".join(_topics(analysis)),
        "title": title,
        "url": str(item.get("url", "")),
        "link_label": "Google News 发现链接" if unverified else "发布页",
        "source": source_name,
        "detail": detail,
        "signal": signal,
        "assets": assets,
        "freshness": _freshness_label(item.get("published_at")),
    }


def _mainlines(items: list[dict[str, Any]], analyses: dict[str, dict[str, Any]], daily_mainlines: list[dict[str, Any]] | None = None) -> list[str]:
    if daily_mainlines:
        return [str(item["headline"]) for item in daily_mainlines]
    candidates = [
        item for item in items
        if not _is_google_news_item(item) or _is_verified_google_item(item)
    ][:5]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        analysis = analyses.get(str(item.get("id")), {})
        region = _region(item)
        topic = _topics(analysis)[0]
        key = (region, topic)
        group = groups.setdefault(key, {"score": 0.0, "items": []})
        group["score"] += float(item.get("relevance_score", 0))
        group["items"].append(item)
    lines: list[str] = []
    for (region, topic), group in sorted(groups.items(), key=lambda entry: entry[1]["score"], reverse=True):
        representative = max(group["items"], key=lambda item: float(item.get("relevance_score", 0)))
        title = _title(representative, analyses.get(str(representative.get("id")), {}))
        suffix = f"（等 {len(group['items'])} 条）" if len(group["items"]) > 1 else ""
        lines.append(f"{region}｜{topic}：{title}{suffix}")
    return lines


def _markdown_mainline(headline: str) -> str:
    """Bold the conclusion label when the Agent uses the required separator."""
    title, separator, detail = headline.partition(" — ")
    return f"**{title}** — {detail}" if separator else headline


def _html_mainline(headline: str) -> str:
    """Escape mainline text while preserving the conclusion-label emphasis."""
    title, separator, detail = headline.partition(" — ")
    if separator:
        return f"<strong>{escape(title)}</strong> — {escape(detail)}"
    return escape(headline)


def _weekly_watchlist_lines(
    items: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    daily_weekly_watchlist: list[str] | None = None,
) -> list[str]:
    if daily_weekly_watchlist:
        return [str(item) for item in daily_weekly_watchlist]
    lines: list[str] = []
    seen: set[str] = set()
    for item in items:
        if _is_google_news_item(item) and not _is_verified_google_item(item):
            continue
        analysis = analyses.get(str(item.get("id", "")), {})
        candidates = analysis.get("signals", []) or [_title(item, analysis)]
        for signal in candidates:
            text = _signal_text(signal)
            if not text:
                continue
            line = f"验证：{text}"
            if line not in seen:
                seen.add(line)
                lines.append(line)
            if len(lines) == 5:
                return lines
    return lines or ["等待下一批已核验宏观数据。"]


# 宏观背景叙述覆盖的国家及其中文标签（按展示顺序）。官方/政府新闻不再逐条
# 铺成表格，而是按国别归并进这三段简短背景里。
MACRO_COUNTRIES = (("CN", "中国"), ("US", "美国"), ("JP", "日本"))
MACRO_BACKGROUND_MAX_ITEMS = 3


def _macro_background(
    items: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    daily_macro_background: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """中美日各一小段宏观背景，由官方/政府条目归并而成（不逐条铺开）。

    当上游（如 LLM 质量门）已合成 ``daily_macro_background``（``国家码 → 文本``）
    时优先采用；否则按国别取该国得分最高的若干条官方条目，拼成一句简短背景。
    """
    if isinstance(daily_macro_background, dict) and any(
        _table_text(value) for value in daily_macro_background.values()
    ):
        # Upstream (LLM) supplied narrative. Always emit中美日三段，缺失的国家
        # 用中性占位句补齐，确保三国都在。
        return [
            (label, _table_text(daily_macro_background.get(code)) or "暂无最新官方动态。")
            for code, label in MACRO_COUNTRIES
        ]

    by_country: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if _item_category(item) != "official":
            continue
        code = str(item.get("source", {}).get("country") or "").strip()
        by_country.setdefault(code, []).append(item)

    lines: list[tuple[str, str]] = []
    for code, label in MACRO_COUNTRIES:
        group = by_country.get(code, [])
        if not group:
            lines.append((label, "暂无最新官方动态。"))
            continue
        parts: list[str] = []
        for item in group[:MACRO_BACKGROUND_MAX_ITEMS]:
            analysis = analyses.get(str(item.get("id", "")), {})
            title = _title(item, analysis)
            source = _table_text(item.get("source", {}).get("name"))
            parts.append(f"{title}（{source}）" if source else title)
        lines.append((label, "；".join(parts) + "。"))
    return lines


def build_daily_briefing_model(
    items: list[dict[str, Any]], analyses: list[dict[str, Any]], report_date: str | None = None,
    daily_mainlines: list[dict[str, Any]] | None = None,
    daily_weekly_watchlist: list[str] | None = None,
    daily_macro_background: dict[str, str] | None = None,
) -> dict[str, Any]:
    analysis_by_id = {str(analysis.get("news_item_id", "")): analysis for analysis in analyses}
    rows = [(item, analysis_by_id.get(str(item.get("id", "")), {})) for item in items]
    official_items = [r for r in rows if _item_category(r[0]) == "official"]
    media_items = [r for r in rows if _item_category(r[0]) == "media"]
    tech_items = [r for r in rows if _item_category(r[0]) == "tech"]
    google_items = [r for r in rows if _item_category(r[0]) == "google"]
    # 热点只保留「财经/科技/AI 以外」的高热条目。
    hot_items = [
        r for r in rows
        if _item_category(r[0]) == "hot" and not _is_finance_or_tech(r[0])
    ]
    mover_rows = []
    for item, analysis in (r for r in rows if _item_category(r[0]) == "movers"):
        row = _row(item, analysis)
        trigger = (item.get("mover") or {}).get("trigger")
        if trigger:
            row["topic"] = _table_text(trigger)
        mover_rows.append(row)
    return {
        "date": report_date or date.today().isoformat(),
        "mainlines": _mainlines(items, analysis_by_id, daily_mainlines),
        "macro_background": _macro_background(items, analysis_by_id, daily_macro_background),
        "weekly_watchlist": _weekly_watchlist_lines(items, analysis_by_id, daily_weekly_watchlist),
        "official": [_row(item, analysis) for item, analysis in official_items],
        "media": [_row(item, analysis) for item, analysis in media_items],
        "tech": [_row(item, analysis) for item, analysis in tech_items],
        "google": [_row(item, analysis) for item, analysis in google_items],
        "hot": [_row(item, analysis) for item, analysis in hot_items],
        "movers": mover_rows,
    }


def _markdown_table(rows: list[dict[str, str]]) -> list[str]:
    lines = ["| 重要性 | 地区 | 主题 | 新闻内容 | 核心信号 | 关注资产 |", "|---|---|---|---|---|---|"]
    for row in rows:
        title = row["title"]
        page_link = f"[{row['link_label']}]({row['url']})" if row["url"] else ""
        source = f"来源：{row['source']}" + (f" {page_link}" if page_link else "")
        freshness = f"；🕒 {row['freshness']}" if row.get("freshness") else ""
        content = f"**{title}**；{row['detail']}；{source}{freshness}"
        lines.append(f"| {row['importance']} | {row['region']} | {row['topic']} | {content} | {row['signal']} | {row['assets']} |")
    return lines


def _market_impact_lines(market_impact: dict[str, str] | None) -> list[str]:
    if not market_impact:
        return [
            "- 利率与债券：待基于已核验来源、增长、通胀、政策预期和流动性数据分析。",
            "- 汇率：待基于已核验来源、相对增长、利差和风险情绪分析。",
            "- 股票与行业：待基于已核验来源、盈利、估值和融资成本分析。",
            "- 商品：待基于已核验来源、供需、库存、美元和实际利率分析。",
            "- 风险偏好与资产配置含义：仅说明非个性化风险暴露变化，不得给出仓位比例或买卖指令。",
        ]
    return [
        f"- 利率与债券：{market_impact['rates_bonds']}",
        f"- 汇率：{market_impact['fx']}",
        f"- 股票与行业：{market_impact['equities']}",
        f"- 商品：{market_impact['commodities']}",
        f"- 风险偏好与资产配置含义：{market_impact['risk_appetite']}",
    ]


def build_daily_briefing(items: list[dict[str, Any]], analyses: list[dict[str, Any]], report_date: str | None = None, market_impact: dict[str, str] | None = None, daily_mainlines: list[dict[str, Any]] | None = None, daily_weekly_watchlist: list[str] | None = None, daily_macro_background: dict[str, str] | None = None) -> str:
    model = build_daily_briefing_model(items, analyses, report_date, daily_mainlines, daily_weekly_watchlist, daily_macro_background)
    lines = ["今日资讯主线"]
    lines.extend(
        f"{index}. {_markdown_mainline(line)}"
        for index, line in enumerate(model["mainlines"], 1)
    )
    if not model["mainlines"]:
        lines.append("1. 暂无可核验的高优先级宏观主题。")
    if model["movers"]:
        lines += ["", "", "📉 异动解读"]
        lines += _markdown_table(model["movers"])
    # 官方/政府消息归并为「中美日宏观背景」三段，不再逐条铺成表格。
    lines += ["", SEPARATOR, "", "🌏 官媒宏观背景（中美日）"]
    lines += [f"- **{label}：** {text}" for label, text in model["macro_background"]]
    lines += ["", "", "🌐 财经媒体新闻"]
    lines += _markdown_table(model["media"]) if model["media"] else ["暂无财经媒体新闻。"]
    lines += ["", "", "💡 科技与AI"]
    lines += _markdown_table(model["tech"]) if model["tech"] else ["暂无科技与AI新闻。"]
    lines += ["", "", "🔍 Google News 快讯"]
    lines += _markdown_table(model["google"]) if model["google"] else ["暂无 Google News 快讯。"]
    lines += ["", "", "🔥 热点速览"]
    lines += _markdown_table(model["hot"]) if model["hot"] else ["暂无热点新闻。"]
    lines += [
        "", SEPARATOR, "", "市场影响地图",
        *_market_impact_lines(market_impact),
        "", SEPARATOR, "", "需要确认的官方数据",
        "- 对新闻中的关键数字、政策措辞和发布时间进行官方来源复核。",
        "", SEPARATOR, "", "本周观察指标",
        *[f"- {line}" for line in model["weekly_watchlist"]],
        "", "免责声明", "这不是投资建议，仅用于新闻理解和宏观分析。",
    ]
    return "\n".join(lines)


def _html_table(rows: list[dict[str, str]]) -> str:
    style = "width:100%;table-layout:fixed;border-collapse:collapse;margin:8px auto 18px;"
    cell = "border:1px solid #d0d7de;padding:7px;vertical-align:top;word-break:break-word;overflow-wrap:anywhere;line-height:1.45;"
    # Email layout: 地区 is stacked above 主题 (5 columns), the summary prose is
    # dropped from 新闻内容, 核心信号 is truncated and 关注资产 gets one item per line.
    widths = ["6%", "10%", "46%", "26%", "12%"]
    # Center the short meta columns; keep the long-text columns left-aligned.
    aligns = ["center", "center", "left", "left", "left"]
    header = "".join(
        f'<th style="{cell}width:{width};background:#f6f8fa;text-align:{align}">{label}</th>'
        for width, align, label in zip(widths, aligns, ["重要性", "主题", "新闻内容", "核心信号", "关注资产"])
    )
    body: list[str] = []
    for row in rows:
        title = escape(row["title"])
        page_link = f' <a href="{escape(row["url"], quote=True)}" style="color:#57606a;text-decoration:underline">{escape(row["link_label"])}</a>' if row["url"] else ""
        freshness = f'；<span style="color:#999;font-size:11px">🕒 {escape(row["freshness"])}</span>' if row.get("freshness") else ""
        content = f"<strong>{title}</strong>；<span style=\"color:#57606a\">来源：{escape(row['source'])}</span>{page_link}{freshness}"
        topic = f"{escape(row['region'])}<br>{escape(row['topic'])}" if row["region"] else escape(row["topic"])
        assets = "<br>".join(escape(asset) for asset in row["assets"].split("、"))
        values = [
            escape(_importance_label(row["importance"])),
            topic,
            content,
            escape(_truncate_signal(row["signal"])),
            assets,
        ]
        body.append(
            "<tr>"
            + "".join(
                f'<td style="{cell}text-align:{aligns[index]}">{value}</td>'
                for index, value in enumerate(values)
            )
            + "</tr>"
        )
    return f'<table role="presentation" style="{style}"><thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def build_daily_briefing_html(items: list[dict[str, Any]], analyses: list[dict[str, Any]], report_date: str | None = None, market_impact: dict[str, str] | None = None, daily_mainlines: list[dict[str, Any]] | None = None, daily_weekly_watchlist: list[str] | None = None, daily_macro_background: dict[str, str] | None = None) -> str:
    model = build_daily_briefing_model(items, analyses, report_date, daily_mainlines, daily_weekly_watchlist, daily_macro_background)
    mainlines = "".join(
        f"<li>{_html_mainline(line)}</li>" for line in model["mainlines"]
    ) or "<li>暂无可核验的高优先级宏观主题。</li>"
    macro_html = "".join(
        f"<li><strong>{escape(label)}：</strong>{escape(text)}</li>"
        for label, text in model["macro_background"]
    ) or "<li>暂无最新官方动态。</li>"
    media = _html_table(model["media"]) if model["media"] else "<p>暂无财经媒体新闻。</p>"
    tech = _html_table(model["tech"]) if model["tech"] else "<p>暂无科技与AI新闻。</p>"
    google = _html_table(model["google"]) if model["google"] else "<p>暂无 Google News 快讯。</p>"
    hot = _html_table(model["hot"]) if model["hot"] else "<p>暂无热点新闻。</p>"
    movers = f"<h2>📉 异动解读</h2>{_html_table(model['movers'])}\n" if model["movers"] else ""
    impact_html = "".join(f"<li>{escape(line[2:])}</li>" for line in _market_impact_lines(market_impact))
    watchlist_html = "".join(f"<li>{escape(line)}</li>" for line in model["weekly_watchlist"])
    return f"""<h2>今日资讯主线</h2><ol>{mainlines}</ol>
{movers}<hr><h2>🌏 官媒宏观背景（中美日）</h2><ul>{macro_html}</ul>
<h2>🌐 财经媒体新闻</h2>{media}
<h2>💡 科技与AI</h2>{tech}
<h2>🔍 Google News 快讯</h2>{google}
<h2>🔥 热点速览</h2>{hot}
<hr><h2>市场影响地图</h2><ul>{impact_html}</ul>
<hr><h2>需要确认的官方数据</h2><p>对新闻中的关键数字、政策措辞和发布时间进行官方来源复核。</p>
<hr><h2>本周观察指标</h2><ul>{watchlist_html}</ul>
<h2>免责声明</h2><p>这不是投资建议，仅用于新闻理解和宏观分析。</p>"""
