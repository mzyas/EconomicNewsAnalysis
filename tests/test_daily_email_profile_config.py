"""Safety boundary tests for the fixed daily delivery profile."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
from unittest.mock import patch

from tools import hermes_plugin
from tools.config_loader import ROOT, load_runtime_config


def _load_native_plugin():
    module_path = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "economic_news_analysis_daily_profile_test",
        module_path,
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load native plugin entry")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self) -> None:
        self.llm = SimpleNamespace()
        self.registration: dict | None = None

    def register_tool(self, **kwargs) -> None:
        self.registration = kwargs


class DailyEmailProfileConfigTests(TestCase):
    def test_native_schema_exposes_only_fixed_daily_profile(self) -> None:
        plugin = _load_native_plugin()
        ctx = _Context()
        plugin.register(ctx)
        assert ctx.registration is not None

        schema = ctx.registration["schema"]
        self.assertEqual(schema["required"], [])
        self.assertEqual(
            schema["properties"]["profile_config"]["enum"], ["daily_email"]
        )
        self.assertNotIn("config_path", schema["properties"])

    def test_delivery_requires_fixed_profile_but_allows_empty_text(self) -> None:
        plugin = _load_native_plugin()
        ctx = _Context()
        plugin.register(ctx)
        assert ctx.registration is not None

        with self.assertRaisesRegex(ValueError, "requires profile_config"):
            ctx.registration["handler"]({"run_mode": "deliver"})

        expected = {"status": "success", "llm_audit": []}
        with mock.patch.object(
            plugin, "run_langgraph_workflow", return_value=expected
        ) as workflow:
            result = json.loads(
                ctx.registration["handler"](
                    {"run_mode": "deliver", "profile_config": "daily_email"}
                )
            )

        self.assertEqual(result, expected)
        workflow.assert_called_once_with(
            ctx, "", run_mode="deliver", profile_config="daily_email"
        )

    def test_profile_file_must_resolve_inside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template = root / "daily_email_briefing.yaml"
            template.write_text("{}\n", encoding="utf-8")

            with patch.object(hermes_plugin, "ROOT", root):
                self.assertEqual(
                    hermes_plugin.resolve_profile_config("daily_email"), template
                )

    def test_profile_mapping_cannot_escape_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root.parent / "outside.yaml"
            outside.write_text("{}\n", encoding="utf-8")
            try:
                with patch.object(hermes_plugin, "ROOT", root), patch.object(
                    hermes_plugin,
                    "_PROFILE_CONFIG_FILENAMES",
                    {"daily_email": "../outside.yaml"},
                ):
                    with self.assertRaisesRegex(ValueError, "must remain inside"):
                        hermes_plugin.resolve_profile_config("daily_email")
            finally:
                outside.unlink(missing_ok=True)

    def test_delivery_loads_only_the_mapped_template(self) -> None:
        ctx = SimpleNamespace(
            llm=SimpleNamespace(),
            get_config=lambda _name, default=None: default,
        )
        runtime_config = {"mode": "deliver"}
        expected = {"status": "success"}
        with patch.object(
            hermes_plugin, "load_runtime_config", return_value=runtime_config
        ) as loader, patch.object(
            hermes_plugin, "run_workflow", return_value=expected
        ) as workflow:
            result = hermes_plugin.run_langgraph_workflow(
                ctx,
                "",
                run_mode="deliver",
                profile_config="daily_email",
            )

        self.assertEqual(result, expected)
        loader.assert_called_once_with(ROOT / "daily_email_briefing.yaml")
        self.assertEqual(workflow.call_args.args[0]["mode"], "deliver")
        self.assertEqual(workflow.call_args.args[0]["stdin_text"], "")
        self.assertEqual(
            workflow.call_args.args[0]["_delivery_trace"],
            {
                "mode": "deliver",
                "profile_config": "daily_email",
                "deliver_email": False,
                "email_send": False,
                "recipient_count": 0,
            },
        )

    def _data_dir(self, home: Path) -> Path:
        return home / "plugin-data" / "economic-news-analysis"

    def test_relative_output_dir_is_anchored_to_profile_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            config = {"output_dir": "output/daily-email-output/"}
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}):
                hermes_plugin.anchor_profile_paths(config)

        self.assertEqual(
            Path(config["output_dir"]),
            self._data_dir(home) / "output" / "daily-email-output",
        )

    def test_log_path_inside_plugin_root_is_rehomed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            config = {"log_path": str(root / "logs" / "daily-workflow.jsonl")}
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                hermes_plugin.anchor_profile_paths(config)

        self.assertEqual(
            Path(config["log_path"]),
            self._data_dir(home) / "logs" / "daily-workflow.jsonl",
        )

    def test_legacy_log_is_copied_once_when_target_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            legacy = root / "logs" / "daily-workflow.jsonl"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"run_id": "old"}\n', encoding="utf-8")
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            target = self._data_dir(home) / "logs" / "daily-workflow.jsonl"
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                hermes_plugin.anchor_profile_paths({"log_path": str(legacy)})
                self.assertEqual(target.read_text(encoding="utf-8"), '{"run_id": "old"}\n')

                target.write_text('{"run_id": "new"}\n', encoding="utf-8")
                legacy.write_text('{"run_id": "older"}\n', encoding="utf-8")
                hermes_plugin.anchor_profile_paths({"log_path": str(legacy)})

            self.assertEqual(target.read_text(encoding="utf-8"), '{"run_id": "new"}\n')
            self.assertTrue(legacy.is_file())

    def test_absolute_outside_root_or_missing_values_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            elsewhere = str(Path(temp_dir) / "elsewhere")
            config = {"output_dir": elsewhere, "log_path": elsewhere + ".jsonl"}
            missing: dict = {"output_dir": None}
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                hermes_plugin.anchor_profile_paths(config)
                hermes_plugin.anchor_profile_paths(missing)

        self.assertEqual(config, {"output_dir": elsewhere, "log_path": elsewhere + ".jsonl"})
        self.assertIsNone(missing["output_dir"])

    def test_paths_escaping_the_profile_data_dir_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            cases = (
                {"output_dir": "../shared"},
                {"output_dir": "output/../../../shared"},
                {"log_path": str(root / ".." / "shared" / "daily-workflow.jsonl")},
            )
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                for config in cases:
                    with self.subTest(config=config):
                        with self.assertRaisesRegex(ValueError, "must remain inside"):
                            hermes_plugin.anchor_profile_paths(dict(config))

    def test_bundled_watchlist_is_seeded_once_and_user_edits_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            bundled = root / "sources" / "stock_watchlist.yaml"
            bundled.parent.mkdir(parents=True)
            bundled.write_text("symbols: []\n", encoding="utf-8")
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            target = self._data_dir(home) / "stock_watchlist.yaml"
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                config = {"market_data": {"watchlist_config": str(bundled)}}
                hermes_plugin.anchor_profile_paths(config)
                self.assertEqual(Path(config["market_data"]["watchlist_config"]), target)
                self.assertEqual(target.read_text(encoding="utf-8"), "symbols: []\n")

                target.write_text("symbols: null\n", encoding="utf-8")
                bundled.write_text("symbols: [] # upgraded\n", encoding="utf-8")
                config = {"market_data": {"watchlist_config": "sources/stock_watchlist.yaml"}}
                hermes_plugin.anchor_profile_paths(config)

            self.assertEqual(Path(config["market_data"]["watchlist_config"]), target)
            self.assertEqual(target.read_text(encoding="utf-8"), "symbols: null\n")

    def test_explicit_or_disabled_watchlist_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            root = Path(temp_dir) / "plugin"
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            elsewhere = str(Path(temp_dir) / "my_watchlist.yaml")
            configs = (
                {"market_data": {"watchlist_config": elsewhere}},
                {"market_data": {"watchlist_config": None}},
                {"market_data": {"watchlist_config": str(root / "sources" / "missing.yaml")}},
            )
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin, "ROOT", root
            ):
                for config in configs:
                    expected = json.loads(json.dumps(config))
                    hermes_plugin.anchor_profile_paths(config)
                    self.assertEqual(config, expected)
            self.assertFalse(self._data_dir(home).exists())

    def test_paths_unchanged_without_hermes(self) -> None:
        config = {"output_dir": "output/daily-email-output/", "log_path": "logs/x.jsonl"}
        with patch.dict(sys.modules, {"hermes_constants": None}):
            hermes_plugin.anchor_profile_paths(config)

        self.assertEqual(
            config, {"output_dir": "output/daily-email-output/", "log_path": "logs/x.jsonl"}
        )

    def test_daily_profile_run_anchors_output_dir_and_log_path(self) -> None:
        ctx = SimpleNamespace(
            llm=SimpleNamespace(),
            get_config=lambda _name, default=None: default,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            fake_constants = SimpleNamespace(get_hermes_home=lambda: home)
            with patch.dict(sys.modules, {"hermes_constants": fake_constants}), patch.object(
                hermes_plugin,
                "load_runtime_config",
                return_value={
                    "output_dir": "output/daily-email-output/",
                    "log_path": str(hermes_plugin.ROOT / "logs" / "daily-workflow.jsonl"),
                },
            ), patch.object(
                hermes_plugin, "run_workflow", return_value={"status": "success"}
            ) as workflow:
                hermes_plugin.run_langgraph_workflow(
                    ctx, "", run_mode="deliver", profile_config="daily_email"
                )

        sent = workflow.call_args.args[0]
        self.assertEqual(
            Path(sent["output_dir"]), self._data_dir(home) / "output" / "daily-email-output"
        )
        self.assertEqual(
            Path(sent["log_path"]), self._data_dir(home) / "logs" / "daily-workflow.jsonl"
        )

    def test_root_template_is_safe_to_track(self) -> None:
        config = load_runtime_config(ROOT / "daily_email_briefing.yaml")

        self.assertTrue(config["deliver_email"])
        self.assertFalse(config["email"]["send"])
        self.assertNotIn("@", config["email"]["from"])
        self.assertNotIn("@", config["email"]["to"])
