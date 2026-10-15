import unittest

from tools.analyzer import analyze_item
from tools.briefing_builder import SEPARATOR, build_daily_briefing, build_daily_briefing_html
from tools.normalizer import normalize_news_item
from tools.relevance_ranker import score_item


class AnalysisBriefingTests(unittest.TestCase):
    def test_english_input_produces_chinese_draft(self):
        item = normalize_news_item(
            {
                "title": "Federal Reserve keeps rates higher as inflation persists",
                "summary": "Officials need more evidence before easing.",
                "source_id": "fed_fomc",
                "source_country": "US",
                "source_language": "en",
            }
        )
        analysis = analyze_item(item, ["USD"])
        self.assertEqual(analysis["output_language"], "zh-CN")
        self.assertIn("该新闻涉及美国", analysis["summary"])
        self.assertTrue(analysis["requires_agent_enrichment"])
        self.assertEqual(analysis["facts"], [])
        self.assertEqual(analysis["signals"], [])
        self.assertEqual(analysis["impact_path"], [])
        self.assertTrue(
            all(value == "" for value in analysis["asset_impact"].values())
        )
        self.assertEqual(
            analysis["source_context"]["summary"],
            "Officials need more evidence before easing.",
        )
        self.assertEqual(analysis["grounding_status"], "source_summary_available")
        self.assertEqual(analysis["focus_assets"], [])
        self.assertIn(
            "不得使用 source_context 之外的近期叙事补全缺失事实。",
            analysis["draft_limitations"],
        )

    def test_briefing_contains_mandatory_sections(self):
        item = score_item(
            normalize_news_item(
                {
                    "title": "央行政策更新",
                    "summary": "政策利率保持不变。",
                    "source_country": "CN",
                    "source_tags": ["monetary_policy"],
                }
            )
        )
        analysis = analyze_item(item)
        report = build_daily_briefing([item], [analysis], "2026-06-10")
        for heading in [
            "今日资讯主线",
            "官媒宏观背景（中美日）",
            "财经媒体新闻",
            "科技与AI",
            "热点速览",
            "Google News 快讯",
            "市场影响地图",
            "需要确认的官方数据",
            "本周观察指标",
            "免责声明",
        ]:
            self.assertIn(heading, report)
        self.assertEqual(report.count(SEPARATOR), 4)
        # 官方条目折叠进中美日宏观背景，不再逐条铺成表格。
        self.assertIn("- **中国：** 央行政策更新。", report)
        self.assertIn("- **美国：** 暂无最新官方动态。", report)
        self.assertIn("- **日本：** 暂无最新官方动态。", report)
        self.assertNotIn("| 中国 | 货币政策 |", report)
        self.assertNotIn("**央行政策更新**；政策利率保持不变。", report)
        self.assertIn("利率与债券：待基于已核验来源", report)
        self.assertIn("不得给出仓位比例或买卖指令", report)
        self.assertNotIn("关注利差预期", report)
        self.assertNotIn("区分增长支持", report)

    def test_briefing_separates_google_news_from_official_items(self):
        official = score_item(
            normalize_news_item(
                {
                    "title": "Official policy update",
                    "summary": "Official summary.",
                    "source_id": "fed_fomc",
                    "source_name": "Federal Reserve",
                    "source_country": "US",
                }
            )
        )
        google_news = score_item(
            normalize_news_item(
                {
                    "title": "Discovery headline only",
                    "summary": "This summary must not appear in the Google News section.",
                    "source_id": "google_news_us_inflation_data",
                    "source_name": "Google News: US CPI PPI inflation data",
                    "source_country": "US",
                    "url": "https://news.google.com/example",
                }
            )
        )
        google_news["ranking_reasons"].append("来源多样性保留")
        google_analysis = analyze_item(google_news)
        google_analysis["translated_title"] = "美国通胀数据快讯"
        report = build_daily_briefing(
            [official, google_news],
            [analyze_item(official), google_analysis],
            "2026-06-21",
        )
        official_section, google_section = report.split(
            "Google News 快讯",
            1,
        )
        # 官方条目折叠进宏观背景叙述（标题+来源），不再单列表格行。
        self.assertIn("Official policy update（Federal Reserve）", official_section)
        self.assertIn("- **美国：**", official_section)
        self.assertNotIn("Discovery headline only", official_section)
        self.assertIn("美国通胀数据快讯", google_section)
        self.assertNotIn("Discovery headline only", google_section)
        self.assertIn("Google News: US CPI PPI inflation data", google_section)
        self.assertIn("原文未核验", google_section)

    def test_google_news_without_translation_is_marked_pending(self):
        google_news = score_item(
            normalize_news_item(
                {
                    "title": "Discovery headline only",
                    "source_id": "google_news_us_inflation_data",
                    "source_country": "US",
                    "source_language": "en",
                }
            )
        )
        report = build_daily_briefing(
            [google_news],
            [analyze_item(google_news)],
            "2026-06-21",
        )
        self.assertIn("待 Agent 忠实翻译：Discovery headline only", report)

    def test_japanese_source_produces_chinese_summary(self):
        item = normalize_news_item(
            {
                "title": "日本 BOJ 金融政策を据え置き",
                "summary": "インフレ見通しを確認する。",
                "source_country": "JP",
                "source_language": "ja",
            }
        )
        analysis = analyze_item(item)
        self.assertTrue(analysis["summary"].startswith("该新闻涉及日本"))
        self.assertNotIn("据え置き", analysis["summary"])
        self.assertEqual(
            analysis["source_context"]["summary"],
            "インフレ見通しを確認する。",
        )

    def test_event_only_summary_is_marked_insufficient(self):
        item = normalize_news_item(
            {
                "title": "Practical Frameworks for Monetary Policy Decisions",
                "summary": (
                    "Speech At the Reykjavík Economic Conference 2026, "
                    "Central Bank of Iceland, Reykjavík, Iceland"
                ),
                "source_id": "fed_speeches",
                "source_country": "US",
                "source_language": "en",
            }
        )
        analysis = analyze_item(item)
        self.assertEqual(
            analysis["grounding_status"],
            "insufficient_source_context",
        )
        self.assertTrue(
            any("非实质信息" in item for item in analysis["draft_limitations"])
        )

    def test_briefing_preserves_full_source_summary(self):
        long_summary = "A" * 1200
        item = score_item(
            normalize_news_item(
                {
                    "title": "Long source summary",
                    "summary": long_summary,
                    "source_name": "Example Source",
                    "source_country": "US",
                    "source_language": "en",
                    "source_category": "news_media",
                }
            )
        )
        analysis = analyze_item(item)
        report = build_daily_briefing([item], [analysis], "2026-06-12")
        self.assertIn("A" * 180 + "…", report)
        self.assertNotIn(long_summary, report)

    def test_mainlines_group_top_five_candidates_and_html_wraps_tables(self):
        items = []
        analyses = []
        for index, score in enumerate([0.9, 0.8, 0.7, 0.6, 0.5, 0.4], 1):
            item = score_item(normalize_news_item({
                "title": f"US CPI update {index}", "summary": "Verified summary.",
                "source_country": "US", "source_name": "Example", "url": f"https://example.com/{index}",
                "source_category": "news_media",
            }))
            item["relevance_score"] = score
            items.append(item)
            analysis = analyze_item(item)
            analysis["topics"] = ["inflation"] if index < 3 else ["employment"]
            analyses.append(analysis)
        report = build_daily_briefing(items, analyses, "2026-06-21")
        self.assertIn("美国｜通胀：US CPI update 1（等 2 条）", report)
        self.assertNotIn("US CPI update 6", report.split("宏观背景", 1)[0])
        html = build_daily_briefing_html(items, analyses, "2026-06-21")
        self.assertIn("table-layout:fixed", html)
        self.assertIn("overflow-wrap:anywhere", html)
        self.assertIn('href="https://example.com/1"', html)
        self.assertNotIn('>US CPI update 1</a>', html)
        self.assertNotIn("<br>", report)

    def test_daily_mainline_and_news_content_presentation(self):
        item = score_item(normalize_news_item({
            "title": "中国5月经济数据发布",
            "summary": "工业增长加速而消费承压，房地产投资仍是固定资产投资的主要拖累。",
            "source_country": "CN",
            "source_name": "国家统计局",
            "url": "https://www.stats.gov.cn/example",
            "source_category": "news_media",
        }))
        analysis = analyze_item(item)
        headline = "中国5月经济数据集中发布：工业加速、消费疲弱、投资分化 — 工业增长加速而消费承压，地产仍是投资拖累。"
        report = build_daily_briefing(
            [item], [analysis], "2026-06-21",
            daily_mainlines=[{
                "headline": headline,
                "supporting_item_ids": [item["id"]],
            }],
        )
        html = build_daily_briefing_html(
            [item], [analysis], "2026-06-21",
            daily_mainlines=[{
                "headline": headline,
                "supporting_item_ids": [item["id"]],
            }],
        )
        self.assertIn("**中国5月经济数据集中发布：工业加速、消费疲弱、投资分化** —", report)
        self.assertIn("**中国5月经济数据发布**；工业增长加速而消费承压", report)
        self.assertLess(
            report.index("工业增长加速而消费承压"),
            report.index("来源：国家统计局"),
        )
        self.assertIn("<strong>中国5月经济数据集中发布：工业加速、消费疲弱、投资分化</strong> —", html)
        self.assertIn("<strong>中国5月经济数据发布</strong>；", html)
        self.assertNotIn("工业增长加速而消费承压，房地产投资", html)
        self.assertIn("width:46%", html)

    def test_html_table_uses_slim_email_layout(self):
        item = score_item(normalize_news_item({
            "title": "Fed holds rates",
            "summary": "Verified summary.",
            "source_country": "US",
            "source_name": "Example",
            "url": "https://example.com/fed",
            "source_category": "news_media",
        }))
        item["relevance_score"] = 0.9
        analysis = analyze_item(item)
        analysis["focus_assets"] = ["美国国债", "美股", "黄金"]
        html = build_daily_briefing_html([item], [analysis], "2026-06-21")
        self.assertNotIn(">地区<", html)
        self.assertEqual(html.count("<th ") % 5, 0)
        self.assertIn(">高</td>", html)
        self.assertNotIn("★", html)
        self.assertIn("美国<br>", html)
        self.assertIn("美国国债<br>美股<br>黄金", html)
        self.assertNotIn("美股、", html)

    def test_official_items_fold_into_macro_background(self):
        ids = {"CN": "pboc_rss", "US": "fed_fomc", "JP": "boj_rss"}
        names = {"CN": "PBoC", "US": "Federal Reserve", "JP": "BOJ"}

        def official(country, title):
            item = score_item(normalize_news_item({
                "title": title,
                "summary": f"{title} summary.",
                "source_id": ids[country],
                "source_name": names[country],
                "source_country": country,
            }))
            return item, analyze_item(item)

        cn, cn_a = official("CN", "PBoC monetary update")
        us, us_a = official("US", "Fed policy update")
        jp, jp_a = official("JP", "BOJ policy update")
        report = build_daily_briefing([cn, us, jp], [cn_a, us_a, jp_a], "2026-06-27")
        macro = report.split("宏观背景（中美日）", 1)[1].split("财经媒体新闻", 1)[0]
        self.assertIn("**中国：** PBoC monetary update（PBoC）。", macro)
        self.assertIn("**美国：** Fed policy update（Federal Reserve）。", macro)
        self.assertIn("**日本：** BOJ policy update（BOJ）。", macro)
        # 官方条目不再以逐条表格出现。
        self.assertNotIn("🏛️ 官方/政府新闻", report)
        self.assertNotIn("| ★", macro)

    def test_llm_macro_background_overrides_deterministic_and_keeps_three(self):
        cn = score_item(normalize_news_item({
            "title": "央行政策更新", "summary": "政策利率保持不变。",
            "source_id": "pboc_rss", "source_name": "PBoC", "source_country": "CN",
        }))
        # LLM supplies CN + US narrative but omits JP; the briefing must still
        # render all three segments, filling JP with the neutral placeholder.
        report = build_daily_briefing(
            [cn], [analyze_item(cn)], "2026-06-27",
            daily_macro_background={
                "CN": "中国货币政策稳健，社融边际改善。",
                "US": "美国通胀回落但仍偏高，美联储观望。",
            },
        )
        macro = report.split("宏观背景（中美日）", 1)[1].split("财经媒体新闻", 1)[0]
        self.assertIn("**中国：** 中国货币政策稳健，社融边际改善。", macro)
        self.assertIn("**美国：** 美国通胀回落但仍偏高，美联储观望。", macro)
        self.assertIn("**日本：** 暂无最新官方动态。", macro)
        # The raw item title should NOT appear once the LLM narrative overrides it.
        self.assertNotIn("央行政策更新（PBoC）", macro)

    def test_tech_and_hot_boards_classify_by_source(self):
        tech = score_item(normalize_news_item({
            "title": "OpenAI ships a new model", "summary": "New frontier model released.",
            "source_id": "techcrunch", "source_name": "TechCrunch", "source_country": "US",
            "source_category": "tech_media",
        }))
        hot = score_item(normalize_news_item({
            "title": "Major earthquake rattles the region", "summary": "Rescue efforts underway.",
            "source_id": "google_news_top_en", "source_name": "Google News: Top Stories", "source_country": "Global",
            "source_category": "hot_search", "url": "https://news.google.com/x",
        }))
        # A finance/tech item from the hot feed must be excluded from 热点.
        hot_finance = score_item(normalize_news_item({
            "title": "Stocks rally as inflation cools", "summary": "Markets jumped on CPI data.",
            "source_id": "google_news_top_en", "source_name": "Google News: Top Stories", "source_country": "US",
            "source_category": "hot_search", "url": "https://news.google.com/y",
        }))
        tech["translated_title"] = "OpenAI 发布新模型"
        hot["translated_title"] = "强震袭击该地区"
        report = build_daily_briefing(
            [tech, hot, hot_finance],
            [analyze_item(tech), analyze_item(hot), analyze_item(hot_finance)],
            "2026-06-27",
        )
        tech_sec = report.split("科技与AI", 1)[1].split("Google News 快讯", 1)[0]
        hot_sec = report.split("热点速览", 1)[1].split("市场影响地图", 1)[0]
        self.assertIn("OpenAI 发布新模型", tech_sec)
        self.assertIn("强震袭击该地区", hot_sec)
        # finance item excluded from 热点
        self.assertNotIn("Stocks rally", hot_sec)
        self.assertNotIn("Stocks rally", report)

    def test_unverified_google_news_uses_discovery_link_label(self):
        item = score_item(normalize_news_item({
            "title": "Unverified discovery", "source_id": "google_news_test",
            "source_name": "Google News", "source_country": "UK",
            "url": "https://news.google.com/example",
        }))
        report = build_daily_briefing([item], [analyze_item(item)], "2026-06-21")
        self.assertIn("英国", report)
        self.assertIn("[Google News 发现链接](https://news.google.com/example)", report)
        self.assertNotIn("[发布页](https://news.google.com/example)", report)


if __name__ == "__main__":
    unittest.main()
