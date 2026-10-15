import unittest
from datetime import date, timedelta

from tools.normalizer import normalize_news_items
from tools.relevance_ranker import balance_by_country, rank_items


def _days_ago(days: int) -> str:
    """Relative date so freshness scoring does not rot as the calendar moves."""
    return (date.today() - timedelta(days=days)).isoformat()


class NormalizerRankerTests(unittest.TestCase):
    def test_normalize_deduplicate_and_rank_official_source(self):
        raw = {
            "title": "Fed inflation update",
            "link": "https://example.com/item",
            "summary": "CPI and monetary policy outlook",
            "source_id": "fed_fomc",
            "source_name": "Federal Reserve",
            "source_country": "US",
            "source_language": "en",
            "source_tags": ["inflation", "monetary_policy"],
        }
        normalized = normalize_news_items([raw, raw])
        self.assertEqual(len(normalized), 1)
        ranked = rank_items(normalized, ["USD"])
        self.assertGreaterEqual(ranked[0]["relevance_score"], 0.6)
        self.assertIn("官方来源", ranked[0]["ranking_reasons"])

    def test_current_data_ranks_above_retrospective_official_content(self):
        retrospective = {
            "title": "居民收支稳步增长——十四五报告之十五",
            "summary": "十四五时期居民收入稳步增长，发展成就显著。",
            "source_id": "nbs_analysis",
            "source_name": "NBS",
            "source_country": "CN",
            "source_tags": ["china_data", "gdp_growth", "employment", "income"],
            "published": "2026-06-13",
        }
        current_data = {
            "title": "2026年5月金融统计数据报告",
            "summary": "5月末M2同比增长，社会融资规模与人民币贷款数据公布。",
            "source_id": "pboc_rss",
            "source_name": "PBoC",
            "source_country": "CN",
            "source_tags": [
                "china_pboc",
                "monetary_policy",
                "fx",
                "cny",
                "financial_stability",
            ],
            "published": "2026-06-13",
        }
        ranked = rank_items(normalize_news_items([retrospective, current_data]))
        self.assertEqual(ranked[0]["title"], current_data["title"])
        self.assertIn(
            "包含当期数据或政策信号",
            ranked[0]["ranking_reasons"],
        )
        self.assertIn(
            "回顾性或总结性内容降权",
            ranked[1]["ranking_reasons"],
        )

    def test_ranking_reserves_relevant_market_discovery_items(self):
        official_items = [
            {
                "title": f"Official policy decision {index}",
                "summary": "Policy rate decision keeps inflation risks under review.",
                "source_id": "fed_fomc",
                "source_name": "Federal Reserve",
                "source_country": "US",
                "source_tags": ["monetary_policy", "inflation"],
                "published": _days_ago(index),
            }
            for index in range(12)
        ]
        discovery_items = [
            {
                "title": f"Gold market inflation outlook {index}",
                "summary": "Gold and bond yields react to inflation expectations.",
                "source_id": f"google_news_market_{index}",
                "source_name": "Google News",
                "source_country": "Global",
                "source_tags": ["inflation", "geopolitics"],
                "published": _days_ago(index),
            }
            for index in range(4)
        ]
        ranked = rank_items(
            normalize_news_items([*official_items, *discovery_items]),
            ["Gold"],
            12,
        )
        discovery = [
            item
            for item in ranked
            if item["source"]["id"].startswith("google_news_")
        ]
        self.assertEqual(len(discovery), 4)
        self.assertTrue(
            any("来源多样性保留" in item["ranking_reasons"] for item in discovery)
        )

    def test_thin_source_context_is_penalized(self):
        thin = {
            "title": "Practical Frameworks for Monetary Policy Decisions",
            "summary": (
                "Speech At the Reykjavík Economic Conference 2026, "
                "Central Bank of Iceland, Reykjavík, Iceland"
            ),
            "source_id": "fed_speeches",
            "source_name": "Federal Reserve",
            "source_country": "US",
            "source_tags": ["monetary_policy"],
            "published": "2026-06-13",
        }
        substantive = {
            "title": "Fed policy update",
            "summary": "Officials kept the policy rate unchanged as inflation remained elevated.",
            "source_id": "fed_press_all",
            "source_name": "Federal Reserve",
            "source_country": "US",
            "source_tags": ["monetary_policy"],
            "published": "2026-06-13",
        }
        ranked = rank_items(normalize_news_items([thin, substantive]))
        self.assertEqual(ranked[0]["title"], substantive["title"])
        self.assertIn("来源上下文不足", ranked[1]["ranking_reasons"])


    def test_japanese_current_signal_is_scored(self):
        ja_item = {
            "title": "日銀、金融政策決定会合の結果を公表",
            "summary": "消費者物価は前年同月比で上昇した。",
            "source_id": "boj_rss",
            "source_name": "Bank of Japan",
            "source_country": "JP",
            "source_language": "ja",
            "source_tags": ["monetary_policy"],
            "published": "2026-06-13",
        }
        ranked = rank_items(normalize_news_items([ja_item]))
        self.assertIn("包含当期数据或政策信号", ranked[0]["ranking_reasons"])

    def test_balance_by_country_caps_dominant_country(self):
        items = [
            {
                "id": f"cn{index}",
                "relevance_score": 0.90 - index * 0.01,
                "source": {"country": "CN"},
                "ranking_reasons": [],
            }
            for index in range(6)
        ] + [
            {
                "id": f"us{index}",
                "relevance_score": 0.50 - index * 0.01,
                "source": {"country": "US"},
                "ranking_reasons": [],
            }
            for index in range(2)
        ]
        balanced = balance_by_country(items, {"CN": 2, "US": 2, "default": 99}, limit=4)
        countries = [item["source"]["country"] for item in balanced]
        self.assertEqual(len(balanced), 4)
        self.assertEqual(countries.count("CN"), 2)
        self.assertEqual(countries.count("US"), 2)

    def test_balance_by_country_backfills_to_preserve_limit(self):
        items = [
            {
                "id": f"cn{index}",
                "relevance_score": 0.90 - index * 0.01,
                "source": {"country": "CN"},
                "ranking_reasons": [],
            }
            for index in range(5)
        ]
        # Only CN items exist; a cap of 2 must still backfill up to the limit.
        balanced = balance_by_country(items, {"CN": 2, "default": 99}, limit=4)
        self.assertEqual(len(balanced), 4)
        self.assertTrue(
            any("国家配额回填" in item["ranking_reasons"] for item in balanced)
        )

    def test_balance_by_country_noop_without_quota(self):
        items = [{"id": "a", "source": {"country": "CN"}, "ranking_reasons": []}]
        self.assertIs(balance_by_country(items, None), items)


if __name__ == "__main__":
    unittest.main()
