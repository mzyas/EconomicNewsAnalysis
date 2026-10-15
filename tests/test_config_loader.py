import os
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from tools._version import __version__ as PKG_VERSION
from tools.config_loader import ROOT, _load_mapping, load_runtime_config
from tools.feed_fetcher import filter_sources
from tools.feed_fetcher_core import load_config


class ConfigLoaderTests(unittest.TestCase):
    def test_skill_defaults_and_example_override(self):
        config = load_runtime_config(ROOT / "examples" / "single_article.yaml")
        # Version is injected from pyproject.toml via tools/_version.py.
        self.assertEqual(config["_skill"]["version"], PKG_VERSION)
        self.assertRegex(config["_skill"]["version"], r"^\d+\.\d+")
        self.assertEqual(config["_skill"]["name"], "economic-news-analysis")
        self.assertEqual(config["mode"], "analyze")
        self.assertEqual(config["output_language"], "zh-CN")
        self.assertEqual(config["max_items_per_source"], 5)
        self.assertEqual(config["max_ranked_items"], 20)
        self.assertFalse(config["market_data"]["enabled"])
        self.assertEqual(config["market_data"]["provider"], "yfinance")

    def test_graph_defaults_in_skill_yaml(self):
        """graph config block provides defaults even when not specified in example."""
        config = load_runtime_config(ROOT / "examples" / "single_article.yaml")
        graph = config.get("graph", {})
        self.assertNotIn("model", graph)
        self.assertEqual(graph.get("temperature"), 0.2)
        self.assertTrue(graph.get("evidence_enabled"))
        self.assertIsNotNone(graph.get("checkpoint_path"))
        self.assertEqual(graph.get("max_quality_retries"), 1)
        self.assertEqual(graph.get("research_max_items"), 5)

    def test_default_log_path_is_scoped_to_skill_root(self):
        config = load_runtime_config()
        self.assertEqual(
            config["log_path"],
            str(ROOT / ".hermes" / "logs" / "daily-workflow.jsonl"),
        )

    def test_graph_defaults_in_daily_briefing(self):
        config = load_runtime_config(ROOT / "examples" / "daily_briefing.yaml")
        graph = config.get("graph", {})
        self.assertNotIn("model", graph)
        self.assertEqual(graph.get("temperature"), 0.2)

    def test_skill_yaml_loads(self):
        skill = _load_mapping(ROOT / "skill.yaml")
        # Version no longer lives in skill.yaml (single source = pyproject.toml).
        self.assertNotIn("version", skill)
        self.assertEqual(skill["name"], "economic-news-analysis")
        self.assertEqual(skill["runtime"]["entrypoint"], "tools/runtime.py")

    def test_env_file_loads_without_overwriting_existing_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "runtime.env"
            env_path.write_text(
                "export TEST_RUNTIME_ONLY=from_env_file\n"
                "EIA_API_KEY=from_env_file\n",
                encoding="utf-8",
            )
            config_path = Path(temp_dir) / "runtime.json"
            config_path.write_text(
                '{"mode":"analyze","env_file":"' + str(env_path).replace("\\", "\\\\") + '"}',
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"EIA_API_KEY": "from_process"}, clear=False):
                config = load_runtime_config(config_path)
                self.assertEqual(config["env_file"], str(env_path))
                self.assertEqual(os.environ["TEST_RUNTIME_ONLY"], "from_env_file")
                self.assertEqual(os.environ["EIA_API_KEY"], "from_process")

    def test_all_example_configs_validate(self):
        for path in (ROOT / "examples").glob("*.yaml"):
            with self.subTest(path=path.name):
                config = load_runtime_config(path)
                self.assertIn(config["mode"], {
                    "analyze",
                    "fetch",
                    "briefing",
                    "deliver",
                    "check",
                    "list-sources",
                    "trend",
                })

    def test_daily_briefing_includes_all_google_news_sources(self):
        for path in [
            ROOT / "examples" / "daily_briefing.yaml",
            ROOT / "daily_email_briefing.yaml",
        ]:
            with self.subTest(config=path.name):
                config = load_runtime_config(path)
                sources = load_config(Path(config["source_config"]))
                selected = filter_sources(sources, config["filters"])
                selected_google = {
                    source.id
                    for source in selected
                    if source.id.startswith("google_news_")
                }
                all_google = {
                    source.id
                    for source in sources
                    if source.id.startswith("google_news_")
                }
                self.assertEqual(selected_google, all_google)
                selected_automatic = {
                    source.id
                    for source in selected
                    if source.type not in {"official_page", "api"}
                }
                all_automatic = {
                    source.id
                    for source in sources
                    if source.type not in {"official_page", "api"}
                }
                self.assertEqual(selected_automatic, all_automatic)
                self.assertEqual(config["max_ranked_items"], 20)
    def test_new_official_api_sources_are_registered(self):
        sources = load_config(ROOT / "sources" / "rss_sources.yaml")
        source_ids = {source.id for source in sources}
        self.assertTrue({
            "bls_public_data_api",
            "bea_api",
            "fred_api",
            "congress_gov_api",
            "sec_edgar_api",
            "eia_api",
            "finra_api",
            "cninfo_api",
            "newyorkfed_markets_api",
            "cabinet_office_rss",
            "meti_statistics_rss",
            "cbo_publications_rss",
            "usda_home_rss",
            "usda_releases_rss",
            "usda_blogs_rss",
        }.issubset(source_ids))


if __name__ == "__main__":
    unittest.main()
