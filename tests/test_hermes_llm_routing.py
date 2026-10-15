"""Contracts for Hermes-managed LLM routing in graph nodes."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from tools.graph.research import ResearchAdapter
from tools.graph.state import initial_state
from tools.graph.workflow import _project_result
from tools.schema_validation import validate_file
from tools.config_loader import ROOT, load_runtime_config
from tools.hermes_plugin import run_langgraph_workflow


class _HermesLLM:
    def __init__(self, text: str, provider: str, model: str) -> None:
        self.text = text
        self.provider = provider
        self.model = model
        self.calls: list[dict] = []

    def complete(self, *, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(text=self.text, provider=self.provider, model=self.model)


class HermesLLMRoutingTests(unittest.TestCase):
    def test_public_result_exposes_llm_audit(self) -> None:
        config = {"mode": "analyze", "_skill": {"name": "test", "version": "1"}}
        state = initial_state(config)
        state["llm_audit"] = [
            {"node": "analysis", "provider": "openrouter", "model": "mimo-test"}
        ]

        result = _project_result(state, config)

        self.assertEqual(result["llm_audit"], state["llm_audit"])
        validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")

    def test_plugin_entry_uses_profile_routes_and_returns_audit(self) -> None:
        llm = _HermesLLM(
            json.dumps({"analysis": "ok", "signals": ["signal"]}),
            provider="openrouter",
            model="xiaomi/mimo-v2-flash",
        )
        ctx = SimpleNamespace(
            llm=llm,
            get_config=lambda name, default=None: {
                "analysis": {
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            } if name == "model_routes" else default,
        )
        config = load_runtime_config()
        config["fetch_enabled"] = False
        config["market_data"]["enabled"] = False
        config["graph"]["checkpoint_path"] = None

        result = run_langgraph_workflow(
            ctx, "A central bank held rates steady.", config=config
        )

        self.assertEqual(result["llm_audit"][0]["node"], "analysis")
        self.assertEqual(result["llm_audit"][0]["model"], "xiaomi/mimo-v2-flash")
    def test_explicit_node_route_uses_facade_and_records_actual_route(self) -> None:
        llm = _HermesLLM(
            json.dumps({"analysis": "ok", "signals": ["signal"]}),
            provider="openrouter",
            model="xiaomi/mimo-v2-flash",
        )
        audit: list[dict] = []
        adapter = ResearchAdapter(
            hermes_llm=llm,
            model_routes={
                "analysis": {
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            },
            llm_audit=audit,
        )

        result = adapter.analyze(
            title="Title",
            source_context={"title": "Title"},
            evidence=[{"url": "u", "content": "evidence"}],
            relevance_score=0.5,
        )

        self.assertEqual(result["signals"], ["signal"])
        self.assertEqual(
            llm.calls[0]["provider"], "openrouter"
        )
        self.assertEqual(llm.calls[0]["model"], "xiaomi/mimo-v2-flash")
        self.assertEqual(
            audit,
            [
                {
                    "node": "analysis",
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            ],
        )

    def test_inherited_node_omits_route_override_and_records_main_route(self) -> None:
        llm = _HermesLLM(
            json.dumps({"analysis": "ok", "signals": ["signal"]}),
            provider="openai",
            model="gpt-5.4",
        )
        audit: list[dict] = []
        adapter = ResearchAdapter(
            hermes_llm=llm,
            model_routes={"analysis": {"inherit_cron_model": True}},
            llm_audit=audit,
        )

        adapter.analyze(
            title="Title",
            source_context={"title": "Title"},
            evidence=[{"url": "u", "content": "evidence"}],
            relevance_score=0.5,
        )

        self.assertNotIn("provider", llm.calls[0])
        self.assertNotIn("model", llm.calls[0])
        self.assertEqual(audit[0]["provider"], "openai")
        self.assertEqual(audit[0]["model"], "gpt-5.4")

    def test_strict_explicit_route_rejects_hermes_fallback(self) -> None:
        llm = _HermesLLM(
            json.dumps({"analysis": "ok", "signals": ["signal"]}),
            provider="openrouter",
            model="different-model",
        )
        adapter = ResearchAdapter(
            hermes_llm=llm,
            model_routes={
                "analysis": {
                    "provider": "openrouter",
                    "model": "xiaomi/mimo-v2-flash",
                }
            },
            strict_model_routes=True,
        )

        result = adapter.analyze(
            title="Title",
            source_context={"title": "Title"},
            evidence=[{"url": "u", "content": "evidence"}],
            relevance_score=0.5,
        )

        self.assertIn("Unexpected LLM route", result["error"])


if __name__ == "__main__":
    unittest.main()
