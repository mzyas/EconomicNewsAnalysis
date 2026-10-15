"""Native Hermes plugin registration contract."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from tools._version import __version__ as PKG_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    module_path = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "economic_news_analysis_native_plugin",
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


class HermesNativePluginTests(TestCase):
    def test_registers_managed_workflow_tool_and_serializes_result(self) -> None:
        plugin = _load_plugin_module()
        ctx = _Context()

        plugin.register(ctx)

        self.assertIsNotNone(ctx.registration)
        assert ctx.registration is not None
        self.assertEqual(ctx.registration["name"], "run_langgraph_workflow")
        self.assertEqual(ctx.registration["toolset"], "economic_news_analysis")
        self.assertEqual(ctx.registration["schema"]["required"], [])
        self.assertEqual(
            ctx.registration["schema"]["properties"]["profile_config"]["enum"],
            ["daily_email"],
        )
        self.assertFalse(ctx.registration["schema"]["additionalProperties"])

        expected = {"status": "success", "llm_audit": []}
        with mock.patch.object(
            plugin, "run_langgraph_workflow", return_value=expected
        ) as workflow:
            encoded = ctx.registration["handler"](
                {"source_text": "A rate decision", "run_mode": "analyze"}
            )

        self.assertEqual(json.loads(encoded), expected)
        workflow.assert_called_once_with(
            ctx, "A rate decision", run_mode="analyze"
        )

    def test_manifest_declares_only_the_workflow_tool(self) -> None:
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")

        self.assertIn(f"version: {PKG_VERSION}", manifest)
        self.assertIn("run_langgraph_workflow", manifest)
        self.assertNotIn("provider:", manifest)
        self.assertNotIn("model:", manifest)


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
