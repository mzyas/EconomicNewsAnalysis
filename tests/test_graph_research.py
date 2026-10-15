"""Tests for LLM research adapter and prompt templates."""

import json
import os
import unittest
from unittest.mock import MagicMock

from tools.graph.research import ResearchAdapter
from tools.graph.prompts import (
    RESEARCH_PROMPT,
    ANALYSIS_PROMPT,
    QUALITY_GATE_PROMPT,
)


class PromptTemplateTests(unittest.TestCase):
    def test_research_prompt_mentions_facts_inference_uncertainty(self):
        self.assertIn("事实", RESEARCH_PROMPT)
        self.assertIn("推断", RESEARCH_PROMPT)
        self.assertIn("不确定性", RESEARCH_PROMPT)
        self.assertIn("{source_context}", RESEARCH_PROMPT)
        self.assertIn("{evidence}", RESEARCH_PROMPT)
        self.assertIn("{language}", RESEARCH_PROMPT)

    def test_analysis_prompt_contains_required_chain(self):
        for token in ("新闻事件", "经济信号", "政策", "资产", "风险"):
            self.assertIn(token, ANALYSIS_PROMPT)
        self.assertIn("{title}", ANALYSIS_PROMPT)
        self.assertIn("{source_context}", ANALYSIS_PROMPT)
        self.assertIn("{evidence}", ANALYSIS_PROMPT)
        self.assertIn("{relevance_score}", ANALYSIS_PROMPT)
        self.assertIn("{language}", ANALYSIS_PROMPT)
        self.assertIn("不构成个性化投资建议", ANALYSIS_PROMPT)

    def test_quality_gate_prompt_contains_evidence_grounding(self):
        self.assertIn("{title}", QUALITY_GATE_PROMPT)
        self.assertIn("{analysis}", QUALITY_GATE_PROMPT)
        self.assertIn("事实", QUALITY_GATE_PROMPT)
        self.assertIn("推断", QUALITY_GATE_PROMPT)
        self.assertIn("不确定性", QUALITY_GATE_PROMPT)


class _RecordingFakeLLM:
    """Fake LLM that records its invocations and returns a fixed response."""

    def __init__(self, response_text: str, raise_exc: Exception | None = None):
        self.response_text = response_text
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def invoke(self, prompt: str):
        self.calls.append({"prompt": prompt})
        if self.raise_exc is not None:
            raise self.raise_exc
        response = MagicMock()
        response.content = self.response_text
        return response


class ResearchAdapterAnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            {
                "url": "https://example.com/a",
                "content": "Federal Reserve raised rates by 25bps.",
                "error": None,
                "truncated": False,
            }
        ]
        self.source_context = {
            "title": "Fed raises rates 25bps",
            "summary": "Title-only discovery item.",
            "url": "https://example.com/a",
        }

    def test_analyze_uses_fake_llm_and_returns_parsed_payload(self):
        payload = {
            "analysis": "美联储加息25个基点。",
            "signals": ["通胀粘性", "利率上行"],
            "focus_assets": ["USD", "US10Y"],
        }
        fake = _RecordingFakeLLM(response_text=json.dumps(payload))
        adapter = ResearchAdapter(temperature=0.2)
        adapter.set_fake_llm(fake)

        result = adapter.analyze(
            title=self.source_context["title"],
            source_context=self.source_context,
            evidence=self.evidence,
            relevance_score=0.9,
            language="zh-CN",
        )

        self.assertEqual(result["analysis"], "美联储加息25个基点。")
        self.assertEqual(result["signals"], ["通胀粘性", "利率上行"])
        self.assertEqual(result["focus_assets"], ["USD", "US10Y"])
        self.assertFalse(result["requires_agent_enrichment"])
        self.assertIsNone(result["error"])
        self.assertEqual(len(fake.calls), 1)
        invoked_prompt = fake.calls[0]["prompt"]
        self.assertIn("Fed raises rates 25bps", invoked_prompt)
        self.assertIn("Federal Reserve raised rates", invoked_prompt)

    def test_analyze_falls_back_when_evidence_is_empty(self):
        fake = _RecordingFakeLLM(response_text="unused")
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.analyze(
            title="Some title",
            source_context={"title": "Some title"},
            evidence=[],
            relevance_score=0.1,
            language="zh-CN",
        )

        self.assertTrue(result["requires_agent_enrichment"])
        self.assertIsNotNone(result["error"])
        # Empty evidence no longer short-circuits: analyze still makes a
        # lightweight title-only LLM attempt, and flags for enrichment only
        # when that yields no parseable signals.
        self.assertTrue(fake.calls)
        self.assertIn("Some title", fake.calls[0]["prompt"])

    def test_analyze_marks_degraded_when_evidence_only_has_errors(self):
        fake = _RecordingFakeLLM(response_text="unused")
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.analyze(
            title="Some title",
            source_context={"title": "Some title"},
            evidence=[
                {"url": "https://x", "content": None, "error": "404", "truncated": False}
            ],
            relevance_score=0.1,
            language="zh-CN",
        )

        self.assertTrue(result["requires_agent_enrichment"])
        self.assertTrue(fake.calls)

    def test_analyze_returns_structured_error_on_llm_failure(self):
        fake = _RecordingFakeLLM(
            response_text="", raise_exc=RuntimeError("boom")
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.analyze(
            title="Title",
            source_context={"title": "Title"},
            evidence=self.evidence,
            relevance_score=0.5,
            language="zh-CN",
        )

        self.assertTrue(result["requires_agent_enrichment"])
        self.assertIsNotNone(result["error"])
        self.assertIn("boom", result["error"])

    def test_analyze_handles_non_json_llm_output_gracefully(self):
        fake = _RecordingFakeLLM(response_text="not json {{{")
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.analyze(
            title="Title",
            source_context={"title": "Title"},
            evidence=self.evidence,
            relevance_score=0.5,
            language="zh-CN",
        )

        # Unparseable output yields no signals even after the retry, so the
        # item must be flagged for enrichment (rather than silently rendering
        # an empty "待补充" signal in the briefing).
        self.assertTrue(result["requires_agent_enrichment"])
        self.assertEqual(result["analysis"], "not json {{{")
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["focus_assets"], [])


class ResearchAdapterResearchTests(unittest.TestCase):
    def test_research_returns_structured_facts_with_fake_llm(self):
        payload = {
            "facts": ["FOMC 升息 25bp", "声明偏鹰"],
            "inference": "美元短期偏强",
            "uncertainty": "未公布点阵图细节",
        }
        fake = _RecordingFakeLLM(response_text=json.dumps(payload))
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.research(
            source_context={"title": "Fed raises rates"},
            evidence=[{"url": "u", "content": "context", "error": None, "truncated": False}],
            language="zh-CN",
        )

        self.assertEqual(result["facts"], payload["facts"])
        self.assertEqual(result["inference"], payload["inference"])
        self.assertEqual(result["uncertainty"], payload["uncertainty"])
        self.assertIsNone(result["error"])
        self.assertEqual(len(fake.calls), 1)

    def test_research_returns_structured_error_on_llm_failure(self):
        fake = _RecordingFakeLLM(
            response_text="", raise_exc=RuntimeError("api down")
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.research(
            source_context={"title": "X"},
            evidence=[{"url": "u", "content": "c", "error": None, "truncated": False}],
            language="zh-CN",
        )

        self.assertEqual(result["facts"], [])
        self.assertIsNotNone(result["error"])
        self.assertIn("api down", result["error"])


class ResearchAdapterQualityCheckTests(unittest.TestCase):
    def test_quality_check_passes_when_evidence_grounded(self):
        fake = _RecordingFakeLLM(
            response_text=json.dumps({"verdict": "PASS", "reason": "事实充分"})
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.quality_check(
            title="Title",
            analysis="Federal Reserve raised rates by 25bps.",
            evidence=[
                {
                    "url": "u",
                    "content": "Federal Reserve raised rates by 25bps in latest meeting.",
                    "error": None,
                    "truncated": False,
                }
            ],
        )

        self.assertEqual(result["verdict"], "PASS")
        self.assertIsNone(result["error"])

    def test_quality_check_fails_when_no_evidence(self):
        fake = _RecordingFakeLLM(
            response_text=json.dumps({"verdict": "FAIL", "reason": "无证据"})
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.quality_check(
            title="Title",
            analysis="Some claim with no grounding.",
            evidence=[],
        )

        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(len(fake.calls), 0)

    def test_quality_check_returns_structured_error_on_llm_failure(self):
        fake = _RecordingFakeLLM(
            response_text="", raise_exc=RuntimeError("rate limit")
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)

        result = adapter.quality_check(
            title="Title",
            analysis="claim",
            evidence=[{"url": "u", "content": "Federal Reserve raised rates", "error": None, "truncated": False}],
        )

        self.assertIsNone(result["verdict"])
        self.assertIsNotNone(result["error"])
        self.assertIn("rate limit", result["error"])


class ResearchAdapterEnvironmentTests(unittest.TestCase):
    def test_analyze_returns_structured_error_when_no_fake_and_no_api_key(self):
        import os

        adapter = ResearchAdapter()
        adapter._fake_llm = None
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = adapter.analyze(
                title="Title",
                source_context={"title": "Title"},
                evidence=[{
                    "url": "u",
                    "content": "Federal Reserve raised rates by 25bps.",
                    "error": None,
                    "truncated": False,
                }],
                relevance_score=0.5,
                language="zh-CN",
            )
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key

        self.assertTrue(result["requires_agent_enrichment"])
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["analysis"], "")
        self.assertEqual(result["signals"], [])


class ResearchAdapterMacroBackgroundTests(unittest.TestCase):
    def test_macro_background_parses_three_country_payload(self):
        payload = {"CN": "中国稳健。", "US": "美国观望。", "JP": "日本温和。"}
        fake = _RecordingFakeLLM(response_text=json.dumps(payload, ensure_ascii=False))
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)
        result = adapter.macro_background("[中国] x\n[美国] y\n[日本] z")
        self.assertEqual(result, payload)
        self.assertIn("中美日宏观背景", fake.calls[0]["prompt"])

    def test_macro_background_keeps_only_nonempty_keys(self):
        payload = {"CN": "中国稳健。", "US": "", "JP": "日本温和。"}
        fake = _RecordingFakeLLM(response_text=json.dumps(payload, ensure_ascii=False))
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)
        result = adapter.macro_background("[中国] x")
        self.assertEqual(result, {"CN": "中国稳健。", "JP": "日本温和。"})

    def test_macro_background_empty_digest_skips_llm(self):
        fake = _RecordingFakeLLM(response_text="{}")
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)
        self.assertEqual(adapter.macro_background("   "), {})
        self.assertEqual(len(fake.calls), 0)

    def test_macro_background_returns_empty_on_unparseable(self):
        fake = _RecordingFakeLLM(response_text="not json at all")
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)
        self.assertEqual(adapter.macro_background("[中国] x"), {})


class ResearchAdapterHermesFacadeTests(unittest.TestCase):
    def test_missing_hermes_facade_fails_loudly(self):
        adapter = ResearchAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter._invoke("hi")
        self.assertIn("requires Hermes ctx.llm", str(ctx.exception))

    def test_fake_llm_keeps_unit_tests_offline(self):
        fake = _RecordingFakeLLM(
            response_text=json.dumps({"signals": ["s"], "analysis": "a"})
        )
        adapter = ResearchAdapter()
        adapter.set_fake_llm(fake)
        result = adapter.analyze(
            title="t",
            source_context={"title": "t"},
            evidence=[{"url": "u", "content": "c"}],
            relevance_score=0.5,
        )
        self.assertEqual(result["signals"], ["s"])
        self.assertEqual(len(fake.calls), 1)

if __name__ == "__main__":
    unittest.main()
