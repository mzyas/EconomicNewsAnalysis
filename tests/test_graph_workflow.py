"""Tests for LangGraph nodes, workflow routing and runtime facade."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tools.config_loader import ROOT, load_runtime_config
from tools.graph.nodes import (
    _has_valid_html,
    _has_valid_markdown,
    _pipeline_status,
    analyze_with_llm_node,
    build_briefing_node,
    collect_evidence_node,
    deliver_email_node,
    fetch_feeds_node,
    fetch_market_data_node,
    google_news_resolve_node,
    load_config_node,
    quality_gate_node,
    rank_items_node,
    write_outputs_node,
    write_run_log_node,
)
from tools.graph.research import ResearchAdapter
from tools.graph.state import initial_state
from tools.graph.workflow import (
    _should_deliver,
    _should_retry_or_downgrade,
    build_workflow,
    run_workflow,
)
from tools.schema_validation import validate_file


def _fake_llm(payload: dict[str, Any] | None = None, text: str | None = None) -> Any:
    """Construct a fake LLM object compatible with ``ResearchAdapter``."""
    mock = MagicMock()
    response = MagicMock()
    response.content = text if text is not None else json.dumps(payload or {})
    mock.invoke.return_value = response
    return mock


def _base_config(
    mode: str = "analyze",
    *,
    fetch_enabled: bool = False,
    deliver_email: bool = False,
    output_dir: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "mode": mode,
        "output_language": "zh-CN",
        "output_format": "json",
        "source_config": str(ROOT / "sources" / "rss_sources.yaml"),
        "max_items_per_source": 5,
        "max_ranked_items": 20,
        "timeout_seconds": 15,
        "fetch_enabled": fetch_enabled,
        "input_items": [
            {
                "title": "Fed signals rate path",
                "summary": "Officials indicated future path of rates.",
                "source_id": "fed_fomc",
                "source_name": "Federal Reserve",
                "source_country": "US",
                "source_language": "en",
                "source_tags": ["monetary_policy", "inflation", "usd"],
            }
        ],
        "focus_assets": ["USD", "JPY", "Gold"],
        "output_dir": output_dir,
        "deliver_email": deliver_email,
        "email": {},
        "filters": {},
        "market_data": {"enabled": False, "provider": "yfinance"},
        "log_path": None,
        "graph": {
            "temperature": 0.2,
            "evidence_enabled": True,
            "checkpoint_path": None,
            "max_quality_retries": 1,
            "research_max_items": 5,
            # Hermetic by default: never read the host's env/credential store
            # during tests. Tests that want real analyses set "_fake_llm".
        },
        "_skill": {"name": "economic-news-analysis", "version": "0.8.0"},
    }
    if extra:
        config.update(extra)
    return config


def _state(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return initial_state(config or _base_config())


# ── Individual node tests ────────────────────────────────────────────────────


class LoadConfigNodeTests(unittest.TestCase):
    def test_marks_phase_loaded_and_pipeline_status_running(self) -> None:
        state = _state()
        update = load_config_node(state)
        self.assertEqual(update["_phase"], "loaded")
        self.assertEqual(update["pipeline_status"], "running")
        self.assertNotIn("status", update)
        self.assertIn("_events", state)
        node_names = [event["node"] for event in state["_events"]]
        self.assertIn("load_config", node_names)


class FetchMarketDataNodeTests(unittest.TestCase):
    def test_disabled_market_data_marks_skipped(self) -> None:
        state = _state()
        update = fetch_market_data_node(state)
        self.assertEqual(update["market_context"]["status"], "skipped")
        self.assertEqual(update["market_context"]["enabled"], False)
        self.assertEqual(update["_phase"], "market_fetched")

    def test_enabled_market_data_attempts_provider_call(self) -> None:
        state = _state()
        state["config"]["market_data"]["enabled"] = True
        with patch("tools.graph.nodes.build_market_context") as mock_ctx:
            mock_ctx.return_value = {
                "enabled": True,
                "provider": "yfinance",
                "status": "success",
                "snapshots": [],
                "errors": [],
            }
            update = fetch_market_data_node(state)
        self.assertEqual(update["market_context"]["status"], "success")
        mock_ctx.assert_called_once()


class FetchFeedsNodeTests(unittest.TestCase):
    def test_fetch_disabled_keeps_input_items(self) -> None:
        state = _state()
        update = fetch_feeds_node(state)
        self.assertEqual(len(update["items"]), 1)
        self.assertEqual(update["items"][0]["title"], "Fed signals rate path")
        self.assertEqual(update["_phase"], "fetched")

    def test_fetch_enabled_calls_fetch_from_config(self) -> None:
        state = _state(_base_config(mode="briefing"))
        state["config"]["fetch_enabled"] = True
        fake = {
            "items": [{"title": "x", "link": "u", "summary": "s", "source_id": "sid"}],
            "total_items": 1,
            "sources_successful": 1,
            "sources_failed": 0,
            "sources_skipped": 0,
            "errors": [],
            "skipped": [],
            "fetched_at": "2026-06-23T00:00:00Z",
        }
        with patch("tools.graph.nodes.fetch_from_config", return_value=fake) as mock:
            update = fetch_feeds_node(state)
        self.assertEqual(update["_items_fetched"], 1)
        self.assertEqual(update["_sources_successful"], 1)
        self.assertEqual(len(update["items"]), 2)
        mock.assert_called_once()


class GoogleNewsResolveNodeTests(unittest.TestCase):
    def test_resolves_google_news_items(self) -> None:
        state = _state()
        state["items"] = [
            {
                "id": "g-1",
                "title": "Macro news",
                "url": "https://news.google.com/x",
                "summary": "stub",
                "discovery": {"channel": "google_news", "original_url": "https://example.com/a"},
            }
        ]
        with patch("tools.graph.nodes.enrich_google_news_items") as mock:
            mock.return_value = (
                [{"id": "g-1", "title": "Macro news", "url": "https://example.com/a",
                  "summary": "real summary", "discovery": {"channel": "google_news", "verification_status": "verified"}}],
                [],
            )
            update = google_news_resolve_node(state)
        self.assertEqual(update["items"][0]["url"], "https://example.com/a")
        self.assertEqual(update["_phase"], "google_resolved")

    def test_skips_when_no_google_items(self) -> None:
        state = _state()
        state["items"] = [{"id": "x", "title": "non-google", "url": "u", "summary": "s"}]
        update = google_news_resolve_node(state)
        self.assertEqual(update["items"][0]["title"], "non-google")
        self.assertEqual(update["_phase"], "google_resolved")


class RankItemsNodeTests(unittest.TestCase):
    def test_ranks_items(self) -> None:
        state = _state()
        state["items"] = [
            {"id": "x", "title": "Macro news", "summary": "S",
             "source": {"id": "fed_fomc", "name": "Fed", "country": "US"},
             "tags": ["monetary_policy", "inflation"]}
        ]
        update = rank_items_node(state)
        self.assertGreaterEqual(len(update["ranked_items"]), 1)
        self.assertEqual(update["_phase"], "ranked")


class CollectEvidenceNodeTests(unittest.TestCase):
    def test_collects_for_research_max_items(self) -> None:
        state = _state()
        for index in range(8):
            state["items"].append(
                {
                    "id": f"x-{index}",
                    "title": f"Item {index}",
                    "summary": f"Summary {index}",
                    "url": f"https://example.com/{index}",
                }
            )
        with patch("tools.graph.nodes.batch_fetch") as mock:
            mock.return_value = [
                {
                    "url": "https://example.com/0",
                    "content": "evidence",
                    "error": None,
                    "truncated": False,
                }
            ]
            update = collect_evidence_node(state, research_max_items=3)
        self.assertLessEqual(len(update["_evidence"]), 3)

    def test_records_error_for_failed_fetch_without_raising(self) -> None:
        state = _state()
        state["items"] = [
            {"id": "i-1", "title": "T", "summary": "S", "url": "https://example.com/x"}
        ]
        with patch("tools.graph.nodes.batch_fetch", return_value=[{
            "url": "https://example.com/x", "content": None,
            "error": "timeout", "truncated": False,
        }]):
            update = collect_evidence_node(state, research_max_items=1)
        self.assertEqual(len(update["errors"]), 1)
        self.assertEqual(update["errors"][0]["type"], "EvidenceFetchError")

    def test_prioritizes_non_aggregator_urls(self) -> None:
        state = _state()
        state["items"] = [
            {
                "id": "a",
                "title": "A",
                "summary": "S",
                "url": "https://news.google.com/rss",
            },
            {
                "id": "b",
                "title": "B",
                "summary": "S",
                "url": "https://publisher.example.com/article",
            },
        ]
        with patch("tools.graph.nodes.batch_fetch") as mock:
            mock.return_value = [{
                "url": "https://publisher.example.com/article",
                "content": "ok",
                "error": None,
                "truncated": False,
            }]
            collect_evidence_node(state, research_max_items=1)
        called_urls = mock.call_args[0][0]
        self.assertEqual(called_urls, ["https://publisher.example.com/article"])


class AnalyzeWithLlmNodeTests(unittest.TestCase):
    def test_marks_requires_enrichment_when_no_evidence(self) -> None:
        state = _state()
        state["ranked_items"] = [
            {"id": "a", "title": "Macro", "summary": "S", "relevance_score": 0.9}
        ]
        update = analyze_with_llm_node(state)
        self.assertEqual(len(update["analysis"]), 1)
        self.assertTrue(update["analysis"][0]["requires_agent_enrichment"])

    def test_marks_requires_enrichment_on_llm_error(self) -> None:
        state = _state()
        state["ranked_items"] = [
            {"id": "a", "title": "Macro", "summary": "S", "relevance_score": 0.9}
        ]
        state["_evidence"] = [
            {"item_id": "a", "url": "u", "content": "evidence", "error": None, "truncated": False}
        ]
        boom = MagicMock()
        boom.invoke.side_effect = RuntimeError("boom")
        state["config"]["graph"]["_fake_llm"] = boom
        update = analyze_with_llm_node(state)
        self.assertTrue(update["analysis"][0]["requires_agent_enrichment"])
        self.assertIn("boom", update["analysis"][0]["error"])


class QualityGateNodeTests(unittest.TestCase):
    def _quality_state(self) -> dict[str, Any]:
        state = _state()
        state["analysis"] = [
            {
                "news_item_id": "a",
                "title": "Macro item",
                "translated_title": "宏观条目",
                "signals": ["inflation"],
                "focus_assets": ["USD"],
                "analysis": "ok",
                "requires_agent_enrichment": False,
            }
        ]
        state["mainlines"] = [{"headline": "h", "supporting_item_ids": ["a"]}]
        state["daily"] = {
            "market_impact": {
                "rates_bonds": "x",
                "fx": "x",
                "equities": "x",
                "commodities": "x",
                "risk_appetite": "x",
            }
        }
        state["items"] = [{"id": "a", "title": "Macro item"}]
        state["_evidence"] = [
            {"item_id": "a", "url": "u", "content": "content", "error": None, "truncated": False}
        ]
        return state

    def test_passes_when_all_criteria_met(self) -> None:
        state = self._quality_state()
        update = quality_gate_node(state)
        self.assertTrue(update["_quality_gate"]["passed"])
        self.assertEqual(update["_phase"], "quality_checked")

    def test_strict_gate_fails_on_untranslated_title(self) -> None:
        # An important item left with an English-only title must block delivery.
        state = self._quality_state()
        state["analysis"][0].pop("translated_title", None)
        state["analysis"][0]["title"] = "Fed holds rates steady"
        state["items"][0]["title"] = "Fed holds rates steady"
        update = quality_gate_node(state)
        self.assertFalse(update["_quality_gate"]["passed"])
        self.assertTrue(
            any(i.startswith("untranslated_titles") for i in update["_quality_gate"]["issues"])
        )

    def test_first_failure_increments_retry_count(self) -> None:
        # With no analyses there are no signals, no derivable mainlines and an
        # empty market-impact map, so the gate must fail.
        state = self._quality_state()
        state["analysis"] = []
        state["items"] = []
        state["mainlines"] = []
        state["daily"] = {}
        state["_retry_count"] = 0
        update = quality_gate_node(state)
        self.assertFalse(update["_quality_gate"]["passed"])
        self.assertEqual(update["_retry_count"], 1)

    def test_subsequent_failure_increments_retry_count(self) -> None:
        # The counter must keep incrementing so max_quality_retries bounds the
        # retry loop (the old behaviour capped it at 1, causing an unbounded loop
        # whenever max_quality_retries >= 2).
        state = self._quality_state()
        state["analysis"] = []
        state["items"] = []
        state["mainlines"] = []
        state["daily"] = {}
        state["_retry_count"] = 1
        update = quality_gate_node(state)
        self.assertFalse(update["_quality_gate"]["passed"])
        self.assertEqual(update["_retry_count"], 2)


class BuildBriefingNodeTests(unittest.TestCase):
    def test_builds_briefing_markdown(self) -> None:
        state = _state()
        state["ranked_items"] = [
            {"id": "a", "title": "Macro", "summary": "S", "relevance_score": 0.9,
             "source": {"name": "Fed", "country": "US"}}
        ]
        state["analysis"] = [
            {
                "news_item_id": "a",
                "title": "Macro",
                "signals": ["inflation"],
                "focus_assets": ["USD"],
                "topics": ["monetary_policy"],
                "translated_title": "宏观",
                "analysis": "detail",
            }
        ]
        update = build_briefing_node(state)
        self.assertIn("briefing_markdown", update)
        self.assertIn("今日资讯主线", update["briefing_markdown"])
        self.assertEqual(update["_phase"], "briefed")

    def test_partial_report_names_missing_sources(self) -> None:
        state = _state()
        state["ranked_items"] = [
            {"id": "a", "title": "Macro", "summary": "S", "relevance_score": 0.9,
             "source": {"name": "Fed", "country": "US"}}
        ]
        state["analysis"] = [{"news_item_id": "a", "signals": ["inflation"]}]
        state["errors"] = [
            {"type": "FetchError", "source_name": "Source A", "message": "timeout"}
        ]
        state["skipped_sources"] = [{"source_name": "Source B", "reason": "disabled"}]

        update = build_briefing_node(state)

        self.assertEqual(update["pipeline_status"], "partial_success")
        self.assertIn("部分成功", update["briefing_markdown"])
        self.assertIn("Source A", update["briefing_markdown"])
        self.assertIn("Source B", update["briefing_markdown"])


class ContentAndPipelineStatusTests(unittest.TestCase):
    def test_markdown_requires_substantive_non_heading_content(self) -> None:
        self.assertFalse(_has_valid_markdown("\ufeff  \n# 财经消息日报\n## 摘要"))
        self.assertFalse(
            _has_valid_markdown(
                "# 财经消息日报\n\n由 Economic News Analysis v0.8.0 自动生成"
            )
        )
        self.assertTrue(_has_valid_markdown("# 财经消息日报\n\n有效正文"))

    def test_html_only_body_is_valid_business_content(self) -> None:
        self.assertTrue(_has_valid_html("<h1>日报</h1><p>有效正文</p>"))
        self.assertFalse(_has_valid_html("<h1>日报</h1>"))
        self.assertFalse(
            _has_valid_html(
                "<p>由 Economic News Analysis v0.8.0 自动生成</p>"
                "<p>数据来源: Yahoo Finance / RSS Feeds</p>"
            )
        )

    def test_pipeline_status_priority_and_independence(self) -> None:
        state = _state()
        self.assertEqual(_pipeline_status(state, has_content=False), "failed")

        state["errors"] = [{"type": "FetchError", "message": "one source failed"}]
        state["output_status"] = "failed"
        state["delivery_status"] = "failed"
        self.assertEqual(_pipeline_status(state, has_content=True), "partial_success")

        state["errors"] = []
        state["_quality_gate"] = {"passed": True, "issues": []}
        self.assertEqual(_pipeline_status(state, has_content=True), "success")


class WriteOutputsNodeTests(unittest.TestCase):
    def test_writes_only_business_markdown_when_output_dir_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = _state()
            state["config"]["output_dir"] = temp_dir
            state["topic"] = "test"
            state["briefing_markdown"] = "# 日报\n\n有效正文"
            update = write_outputs_node(state)
            self.assertIn("output_files", update)
            files = update["output_files"]
            self.assertIn("markdown", files)
            self.assertNotIn("json", files)
            self.assertTrue(Path(files["markdown"]).is_file())

    def test_no_output_dir_leaves_output_files_empty(self) -> None:
        state = _state()
        update = write_outputs_node(state)
        self.assertEqual(update.get("output_files", {}), {})
        self.assertEqual(update["output_status"], "skipped")


class DeliverEmailNodeTests(unittest.TestCase):
    def test_no_email_config_writes_warning(self) -> None:
        state = _state()
        state["config"]["deliver_email"] = False
        update = deliver_email_node(state)
        self.assertIn("warnings", update)

    def test_email_send_disabled_skips_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = _state()
            state["config"]["deliver_email"] = True
            state["config"]["email"] = {
                "from": "a@b.com",
                "to": "c@d.com",
                "subject": "Brief",
                "send": False,
            }
            state["output_files"] = {"markdown": str(Path(temp_dir) / "b.md")}
            Path(state["output_files"]["markdown"]).write_text("# hi\n\nbody", encoding="utf-8")
            update = deliver_email_node(state)
            self.assertEqual(update["delivery_status"], "skipped")
            self.assertEqual(update["delivery_kind"], "none")
            self.assertFalse(update["email_sent"])


class WriteRunLogNodeTests(unittest.TestCase):
    def test_writes_log_when_log_path_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = _state()
            state["config"]["log_path"] = str(Path(temp_dir) / "log.jsonl")
            state["_sources_successful"] = 1
            state["_sources_failed"] = 0
            state["_items_fetched"] = 3
            update = write_run_log_node(state)
            self.assertIn("_run_log_path", update)
            log_path = Path(update["_run_log_path"])
            self.assertTrue(log_path.exists())


# ── Conditional routing tests ────────────────────────────────────────────────


class ShouldRetryOrDowngradeTests(unittest.TestCase):
    def test_returns_retry_when_unmet_and_under_limit(self) -> None:
        state = {"_retry_count": 0, "_quality_gate": {"passed": False}}
        self.assertEqual(_should_retry_or_downgrade(state), "retry")

    def test_returns_downgrade_when_at_limit(self) -> None:
        state = {"_retry_count": 1, "_quality_gate": {"passed": False}}
        self.assertEqual(_should_retry_or_downgrade(state), "downgrade")

    def test_returns_continue_when_passed(self) -> None:
        state = {"_retry_count": 0, "_quality_gate": {"passed": True}}
        self.assertEqual(_should_retry_or_downgrade(state), "continue")


class ShouldDeliverTests(unittest.TestCase):
    def test_returns_deliver_when_approved(self) -> None:
        state = {"_delivery_approved": True, "mode": "deliver"}
        self.assertEqual(_should_deliver(state), "deliver_email")

    def test_returns_skip_when_not_deliver_mode(self) -> None:
        state = {"_delivery_approved": True, "mode": "briefing"}
        self.assertEqual(_should_deliver(state), "write_run_log")


# ── build_workflow / run_workflow integration tests ─────────────────────────


class BuildWorkflowTests(unittest.TestCase):
    def test_returns_compiled_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            graph = build_workflow(checkpoint_path=str(Path(temp_dir) / "cp.sqlite"))
            self.assertTrue(hasattr(graph, "invoke"))
            saver = getattr(graph, "_runtime_checkpoint_saver", None)
            if saver is not None:
                saver.conn.close()


class RunWorkflowModeRoutingTests(unittest.TestCase):
    def test_fetch_mode_returns_ranked_items(self) -> None:
        config = _base_config(mode="fetch")
        result = run_workflow(config, mode="fetch")
        self.assertEqual(result["mode"], "fetch")
        self.assertGreaterEqual(len(result["items"]), 1)
        self.assertIn("analyses", result)
        self.assertEqual(result["analyses"], [])
        self.assertNotIn("briefing_markdown", result)
        self.assertEqual(result["status"], "success")

    def test_analyze_mode_produces_analyses(self) -> None:
        config = _base_config(mode="analyze")
        result = run_workflow(config, mode="analyze")
        self.assertEqual(result["mode"], "analyze")
        self.assertGreaterEqual(len(result["analyses"]), 1)

    def test_briefing_mode_produces_briefing_markdown(self) -> None:
        config = _base_config(mode="briefing")
        result = run_workflow(config, mode="briefing")
        self.assertEqual(result["mode"], "briefing")
        self.assertIn("briefing_markdown", result)
        self.assertNotIn("mml", result["output_files"])

    def test_deliver_mode_with_send_disabled_skips_email(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(
                mode="deliver",
                deliver_email=True,
                output_dir=temp_dir,
            )
            config["email"] = {
                "from": "a@b.com",
                "to": "c@d.com",
                "subject": "Brief",
                "send": False,
            }
            result = run_workflow(config, mode="deliver")
            self.assertEqual(result["mode"], "deliver")
            self.assertNotIn("mml", result["output_files"])
            self.assertFalse(result["email_sent"])
            self.assertEqual(result["delivery_status"], "skipped")
            self.assertEqual(result["delivery_kind"], "none")

    def test_check_mode_returns_check_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(mode="check")
            config["log_path"] = str(Path(temp_dir) / "log.jsonl")
            result = run_workflow(config, mode="check")
            self.assertEqual(result["mode"], "check")
            self.assertIn("check", result)
            self.assertEqual(result["check"]["action"], "fetch")

    def test_list_sources_mode_returns_sources(self) -> None:
        config = _base_config(mode="list-sources")
        result = run_workflow(config, mode="list-sources")
        self.assertEqual(result["mode"], "list-sources")
        self.assertIn("sources", result)
        self.assertGreater(len(result["sources"]), 0)


class RunWorkflowQualityGateTests(unittest.TestCase):
    def test_quality_gate_routes_via_retry_then_downgrade(self) -> None:
        config = _base_config(mode="briefing")
        result = run_workflow(config, mode="briefing")
        # public projection strips `_` fields, but a final attempt that
        # meets the gate will produce briefing_markdown successfully
        self.assertIn("briefing_markdown", result)

    def test_first_failure_routes_to_retry_path(self) -> None:
        state = _state()
        state["_quality_gate"] = {"passed": False}
        state["_retry_count"] = 0
        self.assertEqual(_should_retry_or_downgrade(state), "retry")

    def test_second_failure_routes_to_downgrade_path(self) -> None:
        state = _state()
        state["_quality_gate"] = {"passed": False}
        state["_retry_count"] = 1
        self.assertEqual(_should_retry_or_downgrade(state), "downgrade")


class RunWorkflowPublicResultProjectionTests(unittest.TestCase):
    def test_internal_fields_excluded_from_result(self) -> None:
        config = _base_config(mode="analyze")
        result = run_workflow(config, mode="analyze")
        for key in result:
            self.assertFalse(key.startswith("_"), key)

    def test_result_validates_against_runtime_result_schema(self) -> None:
        config = _base_config(mode="briefing")
        result = run_workflow(config, mode="briefing")
        validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")

    def test_result_with_email_validates_against_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(
                mode="deliver",
                deliver_email=True,
                output_dir=temp_dir,
            )
            config["email"] = {
                "from": "a@b.com",
                "to": "c@d.com",
                "subject": "Brief",
                "send": False,
            }
            result = run_workflow(config, mode="deliver")
            validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")


class RunWorkflowResumeTests(unittest.TestCase):
    def test_run_id_is_preserved(self) -> None:
        config = _base_config(mode="analyze")
        result = run_workflow(config, mode="analyze", run_id="my-run-1")
        self.assertEqual(result["run_id"], "my-run-1")

    def test_resume_loads_existing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(mode="briefing")
            config["graph"]["checkpoint_path"] = str(
                Path(temp_dir) / "cp.sqlite"
            )
            first = run_workflow(
                config, mode="briefing", run_id="resume-run"
            )
            self.assertEqual(first["run_id"], "resume-run")
            resumed = run_workflow(
                config,
                mode="briefing",
                run_id="resume-run",
                resume=True,
            )
            self.assertEqual(resumed["run_id"], "resume-run")


# ── CLI compatibility tests (legacy runtime.py) ─────────────────────────────


class CliCompatibilityTests(unittest.TestCase):
    def test_cli_analyze_still_works(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "runtime.py"),
                "--config",
                str(ROOT / "examples" / "single_article.yaml"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["mode"], "analyze")
        self.assertIn(result["status"], {"success", "partial_success"})
        self.assertIn("skill", result)
        self.assertIn("statistics", result)

    def test_cli_accepts_run_id(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "runtime.py"),
                "--config",
                str(ROOT / "examples" / "single_article.yaml"),
                "--run-id",
                "cli-test-run",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["run_id"], "cli-test-run")

    def test_cli_accepts_resume_flag(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "runtime.py"),
                "--config",
                str(ROOT / "examples" / "single_article.yaml"),
                "--run-id",
                "resumed-run",
                "--resume",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["run_id"], "resumed-run")


if __name__ == "__main__":
    unittest.main()
