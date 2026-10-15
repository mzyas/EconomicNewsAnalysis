import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.config_loader import ROOT, load_runtime_config
from tools.enrichment import (
    apply_enrichment,
    apply_translations,
    load_enrichment,
    load_translations,
    validate_email_readiness,
)
from tools.runtime import execute


class EnrichmentTests(unittest.TestCase):
    def _briefing_result(self):
        config = load_runtime_config(ROOT / "examples" / "offline_briefing.yaml")
        config["input_items"] = [
            {
                "title": "Japan core CPI slows to 1.8%",
                "summary": "Title-only discovery item.",
                "source_id": "google_news_jp_cpi",
                "source_name": "Google News: Japan CPI",
                "source_country": "JP",
                "source_language": "en",
                "url": "https://news.google.com/example",
            }
        ]
        return execute(config)

    def _official_briefing_result(self):
        config = load_runtime_config(ROOT / "examples" / "offline_briefing.yaml")
        config["input_items"] = [
            {
                "title": "Industrial production rises",
                "summary": "Industrial production rose 4.5 percent.",
                "source_id": "fed_fomc",
                "source_name": "Official source",
                "source_country": "US",
            }
        ]
        return execute(config)

    def test_apply_translations_updates_item_analysis_and_briefing(self):
        result = self._briefing_result()
        item_id = result["items"][0]["id"]
        translated = "日本通胀趋于“高止まり”，5月核心 CPI 放缓至 1.8%"
        enriched = apply_translations(result, {item_id: translated})

        self.assertEqual(enriched["items"][0]["translated_title"], translated)
        self.assertEqual(enriched["analyses"][0]["translated_title"], translated)
        self.assertIn(translated, enriched["briefing_markdown"])
        self.assertNotIn("待 Agent 忠实翻译", enriched["briefing_markdown"])

    def test_translation_json_payload_and_cli_are_quote_safe(self):
        result = self._briefing_result()
        item_id = result["items"][0]["id"]
        translated = "日本通胀趋于“高止まり”，5月核心 CPI 放缓至 1.8%"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "runtime.json"
            translations_path = temp_path / "translations.json"
            output_path = temp_path / "enriched.json"
            markdown_path = temp_path / "briefing.md"
            input_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            translations_path.write_text(
                json.dumps(
                    {"translations": [{"news_item_id": item_id, "translated_title": translated}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_translations(translations_path)[item_id], translated)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.enrichment",
                    "--input",
                    str(input_path),
                    "--translations",
                    str(translations_path),
                    "--output",
                    str(output_path),
                    "--markdown-output",
                    str(markdown_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            self.assertEqual(json.loads(completed.stdout)["status"], "success")
            self.assertIn(translated, output_path.read_text(encoding="utf-8"))
            self.assertIn(translated, markdown_path.read_text(encoding="utf-8"))

    def test_unknown_translation_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown news_item_id"):
            apply_translations(self._briefing_result(), {"missing": "标题"})

    def test_structured_patch_allows_only_report_fields(self):
        result = self._briefing_result()
        item_id = result["items"][0]["id"]
        enriched = apply_enrichment(
            result,
            {
                "analyses": [
                    {
                        "news_item_id": item_id,
                        "signals": [{"signal": "通胀放缓"}],
                        "focus_assets": ["JPY"],
                        "topics": ["inflation"],
                    }
                ]
            },
        )
        self.assertEqual(enriched["analyses"][0]["signals"], [{"signal": "通胀放缓"}])
        self.assertEqual(enriched["analyses"][0]["focus_assets"], ["JPY"])
        self.assertEqual(enriched["analyses"][0]["topics"], ["inflation"])
        self.assertIn("来源不足，未提供核心信号", enriched["briefing_markdown"])
        with self.assertRaisesRegex(ValueError, "unexpected key"):
            apply_enrichment(
                result, {"analyses": [{"news_item_id": item_id, "url": "https://bad.example"}]}
            )

    def test_load_enrichment_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            patch = Path(temp_dir) / "patch.json"
            patch.write_text(
                json.dumps({"analyses": [{"news_item_id": "x", "source": "bad"}]}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "unexpected key"):
                load_enrichment(patch)

    def test_structured_patch_rejects_empty_or_unknown_topics(self):
        result = self._briefing_result()
        item_id = result["items"][0]["id"]
        with self.assertRaisesRegex(ValueError, "fewer than"):
            apply_enrichment(result, {"analyses": [{"news_item_id": item_id, "topics": []}]})
        with self.assertRaisesRegex(ValueError, "value is not in enum"):
            apply_enrichment(
                result, {"analyses": [{"news_item_id": item_id, "topics": ["real_estate"]}]}
            )

    def test_email_quality_gate_requires_coverage_and_daily_market_impact(self):
        result = self._official_briefing_result()
        with self.assertRaisesRegex(ValueError, "MML quality gate failed"):
            validate_email_readiness(result)
        item_id = result["items"][0]["id"]
        enriched = apply_enrichment(
            result,
            {
                "analyses": [
                    {
                        "news_item_id": item_id,
                        "signals": [{"signal": "工业产出增长4.5%。"}],
                        "topics": ["gdp_growth"],
                    }
                ],
                "daily": {
                    "market_impact": {
                        "rates_bonds": "增长数据偏强，利率方向待后续数据验证。",
                        "fx": "对美元影响待结合政策预期验证。",
                        "equities": "制造业相关行业受益线索需后续盈利验证。",
                        "commodities": "未见直接商品供需信号。",
                        "risk_appetite": "风险偏好影响有限，等待更多高频数据。",
                    },
                    "mainlines": [
                        {
                            "headline": "工业生产改善但需求韧性仍待后续数据验证，增长动能尚未形成全面扩散并需持续跟踪。",
                            "supporting_item_ids": [item_id],
                        }
                    ],
                    "weekly_watchlist": ["关注后续工业产出与需求数据是否延续改善。"],
                },
            },
        )
        validate_email_readiness(enriched)
        # 官方条目折叠进中美日宏观背景（标题+来源），不再逐条铺出信号列。
        self.assertIn(
            "Industrial production rises（Official source）", enriched["briefing_markdown"]
        )
        self.assertIn("增长数据偏强", enriched["briefing_markdown"])
        self.assertIn("工业生产改善但需求韧性仍待后续数据验证", enriched["briefing_markdown"])
        self.assertIn("关注后续工业产出与需求数据是否延续改善。", enriched["briefing_markdown"])

    def test_email_quality_gate_rejects_mainline_outside_candidates(self):
        result = self._official_briefing_result()
        item_id = result["items"][0]["id"]
        enriched = apply_enrichment(
            result,
            {
                "analyses": [
                    {
                        "news_item_id": item_id,
                        "signals": [{"signal": "工业产出增长4.5%。"}],
                        "topics": ["gdp_growth"],
                    }
                ],
                "daily": {
                    "market_impact": {
                        "rates_bonds": "x",
                        "fx": "x",
                        "equities": "x",
                        "commodities": "x",
                        "risk_appetite": "x",
                    },
                    "mainlines": [
                        {
                            "headline": "该主线故意引用前五候选范围以外的一条新闻，因此必须被质量门槛明确拒绝。",
                            "supporting_item_ids": ["missing"],
                        }
                    ],
                },
            },
        )
        with self.assertRaisesRegex(ValueError, "非候选新闻"):
            validate_email_readiness(enriched)


if __name__ == "__main__":
    unittest.main()
