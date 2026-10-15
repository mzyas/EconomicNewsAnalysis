"""Tests for deliver_email_node idempotency and runtime result projection.

These tests cover the requirements in Task 5:
- Same ``run_id`` must never cause a second ``send_mml`` call.
- Sending only happens when ALL of: quality gate passed, MML built,
  ``deliver_email=True`` and ``email.send=True`` are satisfied.
- ``tools.runtime._project_to_old_schema`` strips every internal field
  (``_*``) so the public payload validates against
  ``schemas/runtime_result.schema.json``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools.config_loader import ROOT, load_runtime_config
from tools.email_delivery import DeliveryResult
from tools.graph.nodes import deliver_email_node
from tools.graph.state import initial_state
from tools.graph.workflow import _project_result, _write_final_runtime_json, run_workflow
from tools.runtime import _project_to_old_schema
from tools.schema_validation import validate_file


# ── Helpers ────────────────────────────────────────────────────────────────


def _base_config(
    mode: str = "deliver",
    *,
    output_dir: str | None = None,
    deliver_email: bool = True,
    email_send: bool = True,
    input_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = input_items if input_items is not None else [
        {
            "title": "Fed signals rate path",
            "summary": "Officials indicated future path of rates.",
            "source_id": "fed_fomc",
            "source_name": "Federal Reserve",
            "source_country": "US",
            "source_language": "en",
            "source_tags": ["monetary_policy", "inflation", "usd"],
        }
    ]
    return {
        "mode": mode,
        "output_language": "zh-CN",
        "output_format": "json",
        "source_config": str(ROOT / "sources" / "rss_sources.yaml"),
        "max_items_per_source": 5,
        "max_ranked_items": 20,
        "timeout_seconds": 15,
        "fetch_enabled": False,
        "input_items": items,
        "focus_assets": ["USD", "JPY", "Gold"],
        "output_dir": output_dir,
        "deliver_email": deliver_email,
        "email": {
            "from": "from@example.com",
            "to": "to@example.com",
            "subject": "Brief",
            "send": email_send,
        },
        "filters": {},
        "market_data": {"enabled": False, "provider": "yfinance"},
        "log_path": None,
        "graph": {
            "temperature": 0.2,
            "evidence_enabled": False,
            "checkpoint_path": None,
            "max_quality_retries": 0,
            "research_max_items": 1,
        },
        "_skill": {"name": "economic-news-analysis", "version": "0.8.0"},
    }


def _make_state(
    config: dict[str, Any] | None = None,
    *,
    output_files: dict[str, str] | None = None,
    delivery: dict[str, Any] | None = None,
    quality_passed: bool = True,
) -> dict[str, Any]:
    state = initial_state(config or _base_config())
    state["output_files"] = dict(output_files or {})
    state["delivery"] = dict(delivery or {})
    state["briefing"] = {
        "markdown": "# brief\n\nsubstantive content",
        "html": "<p>substantive content</p>",
    }
    state["_quality_gate"] = {"passed": quality_passed, "issues": []}
    state["mainlines"] = [{"headline": "h", "supporting_item_ids": ["a"]}]
    state["daily"] = {
        "mainlines": state["mainlines"],
        "market_impact": {
            "rates_bonds": "x",
            "fx": "x",
            "equities": "x",
            "commodities": "x",
            "risk_appetite": "x",
        },
        "weekly_watchlist": [],
    }
    return state


# ── Idempotency ────────────────────────────────────────────────────────────


class DeliverEmailIdempotencyTests(unittest.TestCase):
    def test_second_run_for_same_run_id_does_not_resend(self) -> None:
        """A state re-entry after successful delivery must not send again."""
        state = _make_state(
            delivery={
                "email_sent": True,
                "email_sent_at": "2026-01-01T00:00:00Z",
                "delivery_kind": "report_with_attachment",
            }
        )
        with patch("tools.graph.nodes.send_email") as mock_send:
            update = deliver_email_node(state)
        mock_send.assert_not_called()
        self.assertTrue(update.get("email_sent"))
        self.assertTrue(
            any("已发送" in w or "already sent" in w.lower()
                for w in update.get("warnings", []))
        )

    def test_fresh_run_sends_once_and_records_metadata(self) -> None:
        """First delivery records the durable sent flag and timestamp."""
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "brief.md"
            md_path.write_text("# brief\n\ncontent", encoding="utf-8")
            state = _make_state(output_files={"markdown": str(md_path)})
            with patch(
                "tools.graph.nodes.send_email",
                return_value=DeliveryResult(True, None, attachments=(md_path,)),
            ) as mock_send:
                update = deliver_email_node(state)
        mock_send.assert_called_once()
        self.assertTrue(update.get("email_sent"))
        delivery = update.get("delivery", {})
        self.assertTrue(delivery.get("email_sent"))
        self.assertIn("email_sent_at", delivery)


# ── Delivery conditions ────────────────────────────────────────────────────


class DeliverEmailConditionsTests(unittest.TestCase):
    def test_no_send_when_deliver_email_disabled(self) -> None:
        config = _base_config(deliver_email=False, email_send=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "brief.md"
            md_path.write_text("# brief\n\ncontent", encoding="utf-8")
            state = _make_state(config=config, output_files={"markdown": str(md_path)})
            with patch("tools.graph.nodes.send_email") as mock_send:
                update = deliver_email_node(state)
        mock_send.assert_not_called()
        self.assertFalse(update.get("email_sent"))
        self.assertTrue(
            any("deliver_email" in w for w in update.get("warnings", []))
        )

    def test_no_send_when_email_send_flag_false(self) -> None:
        config = _base_config(deliver_email=True, email_send=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "brief.md"
            md_path.write_text("# brief\n\ncontent", encoding="utf-8")
            state = _make_state(config=config, output_files={"markdown": str(md_path)})
            with patch("tools.graph.nodes.send_email") as mock_send:
                update = deliver_email_node(state)
        mock_send.assert_not_called()
        self.assertFalse(update.get("email_sent"))
        self.assertEqual(update["delivery_status"], "skipped")
        self.assertEqual(update["delivery_kind"], "none")
        self.assertTrue(update["delivery_requested"])
        self.assertEqual(update["delivery_skip_reason"], "email_send_disabled")
        self.assertEqual(
            update["effective_delivery_config"],
            {
                "mode": "deliver",
                "profile_config": None,
                "deliver_email": True,
                "email_send": False,
                "recipient_count": 1,
            },
        )

    def test_quality_gate_failure_sends_partial_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "brief.md"
            md_path.write_text("# brief\n\ncontent", encoding="utf-8")
            state = _make_state(
                output_files={"markdown": str(md_path)},
                quality_passed=False,
            )
            with patch(
                "tools.graph.nodes.send_email",
                return_value=DeliveryResult(True, None, attachments=(md_path,)),
            ) as mock_send:
                update = deliver_email_node(state)
        mock_send.assert_called_once()
        self.assertTrue(update.get("email_sent"))
        self.assertEqual(update["pipeline_status"], "partial_success")

    def test_delivery_failure_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "brief.md"
            md_path.write_text("# brief\n\ncontent", encoding="utf-8")
            state = _make_state(output_files={"markdown": str(md_path)})
            with patch(
                "tools.graph.nodes.send_email",
                return_value=DeliveryResult(False, None, error="Missing email config fields: from"),
            ):
                update = deliver_email_node(state)
        self.assertFalse(update.get("email_sent"))
        self.assertTrue(
            any("Missing email config" in w for w in update.get("warnings", []))
        )

    def test_no_markdown_artifact_sends_body_only(self) -> None:
        state = _make_state(output_files={})
        with patch(
            "tools.graph.nodes.send_email",
            return_value=DeliveryResult(True, None),
        ) as mock_send:
            update = deliver_email_node(state)
        mock_send.assert_called_once()
        self.assertTrue(update.get("email_sent"))
        self.assertEqual(update["delivery_kind"], "report_without_attachment")


class DeliveryFallbackContractTests(unittest.TestCase):
    def test_valid_attachment_sends_report_with_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "report.md"
            markdown.write_text("# 日报\n\n有效正文", encoding="utf-8")
            state = _make_state(output_files={"markdown": str(markdown)})
            state["pipeline_status"] = "success"
            state["output_status"] = "success"
            result = DeliveryResult(True, None, attachments=(markdown,))

            with patch("tools.graph.nodes.send_email", return_value=result) as mock_send:
                update = deliver_email_node(state)

        self.assertTrue(update["email_sent"])
        self.assertEqual(update["delivery_status"], "success")
        self.assertEqual(update["delivery_kind"], "report_with_attachment")
        self.assertEqual(update["pipeline_status"], "success")
        self.assertEqual(update["output_status"], "success")
        self.assertEqual(mock_send.call_args.kwargs["attachments"], (markdown,))

    def test_html_only_report_sends_without_attachment(self) -> None:
        state = _make_state(output_files={})
        state["briefing"] = {"markdown": "", "html": "<p>HTML 有效正文</p>"}
        state["pipeline_status"] = "success"
        with patch(
            "tools.graph.nodes.send_email",
            return_value=DeliveryResult(True, None),
        ) as mock_send:
            update = deliver_email_node(state)

        self.assertTrue(update["email_sent"])
        self.assertEqual(update["delivery_kind"], "report_without_attachment")
        self.assertEqual(mock_send.call_args.kwargs["attachments"], ())

    def test_no_business_content_sends_failure_notification(self) -> None:
        state = _make_state(output_files={})
        state["briefing"] = {"markdown": "# 日报", "html": "<h1>日报</h1>"}
        with patch(
            "tools.graph.nodes.send_email",
            return_value=DeliveryResult(True, None),
        ):
            update = deliver_email_node(state)

        self.assertEqual(update["pipeline_status"], "failed")
        self.assertEqual(update["output_status"], "skipped")
        self.assertEqual(update["delivery_status"], "success")
        self.assertEqual(update["delivery_kind"], "failure_notification")
        self.assertTrue(update["email_sent"])

    def test_send_failure_does_not_change_pipeline_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "report.md"
            markdown.write_text("# 日报\n\n有效正文", encoding="utf-8")
            state = _make_state(output_files={"markdown": str(markdown)})
            state["pipeline_status"] = "success"
            state["output_status"] = "success"
            with patch(
                "tools.graph.nodes.send_email",
                return_value=DeliveryResult(False, None, error="smtp failed"),
            ):
                update = deliver_email_node(state)

        self.assertEqual(update["pipeline_status"], "success")
        self.assertEqual(update["output_status"], "success")
        self.assertEqual(update["delivery_status"], "failed")
        self.assertEqual(update["delivery_kind"], "report_with_attachment")
        self.assertFalse(update["email_sent"])
        self.assertTrue(any("smtp failed" in warning for warning in update["warnings"]))


def _passing_quality_gate(state: dict[str, Any]) -> dict[str, Any]:
    """Stand-in for ``quality_gate_node`` that always reports a passing gate.

    The deliver_email_node + checkpoint idempotency tests focus on the
    deliver path, not on the upstream evidence / LLM wiring. Replacing
    ``quality_gate_node`` with a deterministic pass keeps the workflow
    tests focused on the behavior under test.
    """
    return {
        "_quality_gate": {
            "passed": True,
            "issues": [],
            "checked_at": "2026-01-01T00:00:00Z",
        },
        "_retry_count": 0,
        "_phase": "quality_checked",
    }


class DeliverEmailViaWorkflowTests(unittest.TestCase):
    def test_workflow_with_checkpoint_does_not_resend(self) -> None:
        """A resumed run for the same run_id must not trigger a second send."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(
                output_dir=temp_dir,
                deliver_email=True,
                email_send=True,
            )
            config["graph"]["checkpoint_path"] = str(
                Path(temp_dir) / "cp.sqlite"
            )
            delivery_result = DeliveryResult(True, None)
            with patch(
                    "tools.graph.nodes.send_email",
                    return_value=delivery_result,
                 ) as mock_send, patch(
                     "tools.graph.workflow.quality_gate_node",
                     side_effect=_passing_quality_gate,
                 ):
                first = run_workflow(
                    config, mode="deliver", run_id="idempotent-run"
                )
                first_send_calls = mock_send.call_count
                second = run_workflow(
                    config,
                    mode="deliver",
                    run_id="idempotent-run",
                    resume=True,
                )
                second_send_calls = mock_send.call_count
            self.assertGreaterEqual(first_send_calls, 1)
            self.assertEqual(second_send_calls, first_send_calls)
            self.assertTrue(first.get("email_sent"))
            self.assertTrue(second.get("email_sent"))


# ── Result projection ─────────────────────────────────────────────────────


class ProjectToOldSchemaTests(unittest.TestCase):
    def test_projection_sets_status_only_from_pipeline_status(self) -> None:
        state = {
            "run_id": "x",
            "mode": "deliver",
            "pipeline_status": "partial_success",
            "status": "success",
            "output_status": "success",
            "delivery_status": "success",
            "delivery_kind": "report_with_attachment",
            "email_sent": True,
        }
        projected = _project_result(state, _base_config())
        self.assertEqual(projected["status"], "partial_success")
        self.assertEqual(projected["status"], projected["pipeline_status"])

    def test_final_runtime_json_contains_delivery_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "report.md"
            markdown.write_text("# 日报\n\n有效正文", encoding="utf-8")
            result = {
                "run_id": "run-final-json",
                "mode": "deliver",
                "status": "success",
                "pipeline_status": "success",
                "output_status": "success",
                "delivery_status": "failed",
                "delivery_kind": "report_with_attachment",
                "email_sent": False,
                "briefing_markdown": "# 日报\n\n有效正文",
                "warnings": ["邮件发送失败: smtp failed"],
                "output_files": {"markdown": str(markdown)},
            }
            finalized = _write_final_runtime_json(
                result, {"output_dir": temp_dir}
            )

            saved = json.loads(
                Path(finalized["output_files"]["json"]).read_text(encoding="utf-8")
            )
        self.assertEqual(saved, finalized)
        self.assertEqual(saved["delivery_status"], "failed")
        self.assertEqual(saved["delivery_kind"], "report_with_attachment")
        self.assertFalse(saved["email_sent"])

    def test_final_json_failure_does_not_change_pipeline_or_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown = Path(temp_dir) / "report.md"
            markdown.write_text("# 日报\n\n有效正文", encoding="utf-8")
            result = {
                "run_id": "run-json-failure",
                "mode": "deliver",
                "status": "success",
                "pipeline_status": "success",
                "output_status": "success",
                "delivery_status": "success",
                "delivery_kind": "report_with_attachment",
                "email_sent": True,
                "briefing_markdown": "# 日报\n\n有效正文",
                "warnings": [],
                "output_files": {"markdown": str(markdown)},
            }
            with patch.object(Path, "replace", side_effect=OSError("disk full")):
                finalized = _write_final_runtime_json(result, {"output_dir": temp_dir})

        self.assertEqual(finalized["pipeline_status"], "success")
        self.assertEqual(finalized["delivery_status"], "success")
        self.assertEqual(finalized["output_status"], "partial_success")
        self.assertNotIn("json", finalized["output_files"])
        self.assertTrue(any("disk full" in warning for warning in finalized["warnings"]))
    def test_strips_every_internal_field(self) -> None:
        state: dict[str, Any] = {
            "run_id": "abc",
            "mode": "analyze",
            "status": "success",
            "pipeline_status": "success",
            "output_status": "skipped",
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z",
            "items": [],
            "analyses": [],
            "errors": [],
            "warnings": [],
            "output_files": {},
            "_phase": "delivered",
            "_evidence": [{"item_id": "a", "url": "u", "content": "c"}],
            "_retry_count": 2,
            "_quality_gate": {"passed": True},
            "_events": [{"node": "load_config"}],
            "_items_fetched": 5,
            "_sources_successful": 1,
            "_sources_failed": 0,
            "_sources_skipped": 0,
            "_research_queue": [],
        }
        projected = _project_to_old_schema(state)
        for key in projected:
            self.assertFalse(key.startswith("_"), key)
        for internal in (
            "_phase", "_evidence", "_retry_count", "_quality_gate",
            "_events", "_items_fetched", "_sources_successful",
            "_sources_failed", "_sources_skipped", "_research_queue",
        ):
            self.assertNotIn(internal, projected)
        self.assertEqual(projected["run_id"], "abc")

    def test_drops_unknown_keys_and_delivery_subfield(self) -> None:
        state: dict[str, Any] = {
            "run_id": "x",
            "mode": "analyze",
            "status": "success",
            "pipeline_status": "success",
            "output_status": "skipped",
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z",
            "output_language": "zh-CN",
            "statistics": {
                "sources_successful": 0,
                "sources_failed": 0,
                "sources_skipped": 0,
                "items_fetched": 0,
                "items_normalized": 0,
                "items_ranked": 0,
                "items_analyzed": 0,
            },
            "items": [],
            "analyses": [],
            "errors": [],
            "warnings": [],
            "output_files": {},
            "skill": {"name": "test", "version": "0"},
            "secret_key_that_should_not_leak": "leaked",
            "delivery": {"email_sent": True, "mml_path": "/tmp/x.mml"},
        }
        projected = _project_to_old_schema(state)
        self.assertNotIn("secret_key_that_should_not_leak", projected)
        self.assertNotIn("delivery", projected)
        validate_file(projected, ROOT / "schemas" / "runtime_result.schema.json")

    def test_projected_payload_validates_against_schema(self) -> None:
        config = _base_config(mode="analyze", deliver_email=False, email_send=False)
        result = run_workflow(config, mode="analyze")
        reprojected = _project_to_old_schema(result)
        validate_file(reprojected, ROOT / "schemas" / "runtime_result.schema.json")

    def test_workflow_result_has_no_internal_fields(self) -> None:
        config = _base_config(mode="analyze", deliver_email=False, email_send=False)
        result = run_workflow(config, mode="analyze")
        for key in result:
            self.assertFalse(key.startswith("_"), key)


# ── End-to-end smoke ──────────────────────────────────────────────────────


class EndToEndDeliverySmokeTests(unittest.TestCase):
    def test_deliver_mode_validates_against_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(
                output_dir=temp_dir,
                deliver_email=True,
                email_send=False,
            )
            result = run_workflow(config, mode="deliver")
            validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")
            self.assertNotIn("mml", result["output_files"])
            self.assertFalse(result["email_sent"])
            self.assertEqual(result["delivery_status"], "skipped")

    def test_deliver_mode_with_send_writes_email_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _base_config(
                output_dir=temp_dir,
                deliver_email=True,
                email_send=True,
            )
            with patch(
                    "tools.graph.nodes.send_email",
                    return_value=DeliveryResult(True, None),
                 ) as mock_send, patch(
                     "tools.graph.workflow.quality_gate_node",
                     side_effect=_passing_quality_gate,
                 ):
                result = run_workflow(config, mode="deliver")
            mock_send.assert_called()
            self.assertTrue(result["email_sent"])


# ── Legacy example configs still load ─────────────────────────────────────


class LegacyExampleConfigTests(unittest.TestCase):
    def test_all_examples_load(self) -> None:
        config_paths = [
            ROOT / "examples" / name
            for name in (
                "single_article.yaml",
                "offline_briefing.yaml",
                "daily_briefing.yaml",
                "fetch_only.yaml",
                "source_check.yaml",
            )
        ] + [ROOT / "daily_email_briefing.yaml"]
        for path in config_paths:
            with self.subTest(example=path.name):
                config = load_runtime_config(path)
                self.assertIn("mode", config)
                self.assertIn("_skill", config)

if __name__ == "__main__":
    unittest.main()
