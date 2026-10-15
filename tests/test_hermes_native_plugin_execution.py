"""End-to-end offline execution through the native Hermes plugin handler."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


def _load_like_hermes():
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []
    parent.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = parent

    name = "hermes_plugins.economic_news_analysis_execution_test"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(ROOT)]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _LLM:
    def complete(self, *, messages, **kwargs):
        return SimpleNamespace(
            text=json.dumps({"analysis": "ok", "signals": ["rate-hold"]}),
            provider="openrouter",
            model="xiaomi/mimo-v2-flash",
        )


class _Context:
    llm = _LLM()

    def get_config(self, name, default=None):
        if name == "model_routes":
            return {
                "analysis": {
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            }
        return default

    def register_tool(self, **kwargs):
        self.registration = kwargs


class HermesNativePluginExecutionTests(TestCase):
    def test_registered_handler_runs_graph_and_returns_route_audit(self) -> None:
        plugin = _load_like_hermes()
        ctx = _Context()
        plugin.register(ctx)

        result = json.loads(
            ctx.registration["handler"](
                {
                    "source_text": "A central bank held rates steady.",
                    "run_mode": "analyze",
                }
            )
        )

        self.assertEqual(
            result["llm_audit"],
            [
                {
                    "node": "analysis",
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
