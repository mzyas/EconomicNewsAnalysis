"""Compact summary returned to the Cron agent for deliver/check runs."""

from __future__ import annotations

import json
import unittest
from typing import Any

from tools.result_summary import SUMMARY_MODES, summarize_result

_UNSCOPED = (
    "llm error: Hermes could not read this profile's ANTHROPIC_TOKEN (an internal "
    "profile-scoping bug on the multiplexed gateway, not your configuration)."
)
_STATUS_ORDER = [
    "result_kind",
    "run_id",
    "mode",
    "pipeline_status",
    "output_status",
    "delivery_status",
    "delivery_kind",
    "delivery_requested",
    "email_send_attempted",
    "delivery_skip_reason",
    "email_sent",
    "effective_delivery_config",
    "started_at",
    "completed_at",
]


def _full_result(*, failing: int = 53, total: int = 53) -> dict[str, Any]:
    big = "x" * 5000
    analyses = []
    for n in range(total):
        failed = n < failing
        analyses.append(
            {
                "news_item_id": str(n),
                "analysis_method": "heuristic_draft",
                "requires_agent_enrichment": failed,
                "analysis": big,
                "source_context": {"summary": big},
                **({"llm_error": _UNSCOPED, "error": _UNSCOPED} if failed else {}),
                **({} if failed else {"translated_title": "宏观"}),
            }
        )
    return {
        "run_id": "run-1",
        "mode": "deliver",
        "pipeline_status": "partial_success",
        "output_status": "success",
        "delivery_status": "success",
        "delivery_requested": True,
        "email_send_attempted": True,
        "delivery_skip_reason": None,
        "effective_delivery_config": {"send": True},
        "delivery_kind": "daily_email",
        "started_at": "2026-10-19T00:00:00+00:00",
        "completed_at": "2026-10-19T00:20:00+00:00",
        "items": [{"id": str(n), "summary": big} for n in range(total)],
        "analyses": analyses,
        "llm_audit": (
            [{"node": "quality", "provider": "xiaomi", "model": "mimo"}] * 12
            + [{"node": "market_impact", "provider": "xiaomi", "model": "mimo"}]
        ),
        # Same shapes and distribution as the producers wrote in a real deliver run.
        "errors": (
            [
                {
                    "type": "PublisherResolveError",
                    "id": str(n),
                    "title": f"title {n}",
                    "message": "未解析到发布者页面",
                }
                for n in range(140)
            ]
            + [
                {
                    "type": "SourceFetchError",
                    "source_id": f"s{n}",
                    "source_name": f"Source {n}",
                    "message": message,
                }
                for n, message in enumerate(["HTTP 403"] * 4 + ["HTTP 404"] * 2 + ["HTTP 429"])
            ]
        ),
        "warnings": ["质量门控未通过: untranslated_titles"],
        "output_files": {"markdown": "/out/r.md", "json": "/out/run-1_runtime.json"},
        "email_sent": True,
        "briefing_markdown": big * 8,
        "market_context": {"blob": big * 4},
        "trend_table": {"blob": big * 2},
        "statistics": {"items_analyzed": total},
        "run_summary": {
            "total_seconds": 900,
            "reserve_seconds": 60,
            "remaining_seconds": 0.0,
            "degraded": False,
            "degradation_reason": None,
            "blocked_stage": None,
            "blocked_item": None,
            "checkpoints": [
                {
                    "stage": "analysis",
                    "status": "completed",
                    "duration_seconds": 0.8,
                    "started_monotonic": 1.0,
                    "completed_monotonic": 1.8,
                    "remaining_seconds": 800.0,
                }
            ],
        },
    }


class SummarizeResultTests(unittest.TestCase):
    def test_modes_cover_deliver_and_check_only(self) -> None:
        self.assertEqual(SUMMARY_MODES, {"deliver", "check"})

    def test_summary_is_small_and_status_fields_come_first(self) -> None:
        full = _full_result()
        self.assertGreater(len(json.dumps(full)), 400_000)

        summary = summarize_result(full)

        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 20_000)
        self.assertEqual(list(summary)[: len(_STATUS_ORDER)], _STATUS_ORDER)
        self.assertEqual(summary["output_files"]["json"], "/out/run-1_runtime.json")
        self.assertEqual(summary["full_result_path"], "/out/run-1_runtime.json")
        self.assertEqual(summary["statistics"], {"items_analyzed": 53})
        self.assertEqual(summary["warnings"], ["质量门控未通过: untranslated_titles"])
        for dropped in ("items", "briefing_markdown", "market_context", "trend_table"):
            self.assertNotIn(dropped, summary)

    def test_run_summary_keeps_budget_and_trimmed_checkpoints(self) -> None:
        run_summary = summarize_result(_full_result())["run_summary"]

        self.assertEqual(run_summary["remaining_seconds"], 0.0)
        self.assertFalse(run_summary["degraded"])
        self.assertEqual(
            run_summary["checkpoints"],
            [{"stage": "analysis", "status": "completed", "duration_seconds": 0.8}],
        )

    def test_errors_are_counted_grouped_and_sampled(self) -> None:
        errors = summarize_result(_full_result())["errors"]

        self.assertEqual(errors["total"], 147)
        self.assertEqual(errors["by_type"], {"PublisherResolveError": 140, "SourceFetchError": 7})
        self.assertEqual(
            errors["top_messages"],
            [
                {"type": "PublisherResolveError", "message": "未解析到发布者页面", "count": 140},
                {"type": "SourceFetchError", "message": "HTTP 403", "count": 4},
                {"type": "SourceFetchError", "message": "HTTP 404", "count": 2},
                {"type": "SourceFetchError", "message": "HTTP 429", "count": 1},
            ],
        )
        self.assertEqual(len(errors["first"]), 10)

    def test_sampled_error_messages_are_clipped(self) -> None:
        entries = [{"type": "FetchError", "message": "x" * 5000}] * 3
        errors = summarize_result({"errors": entries})["errors"]

        self.assertTrue(all(len(e["message"]) <= 300 for e in errors["first"]))

    def test_llm_audit_is_aggregated(self) -> None:
        self.assertEqual(
            summarize_result(_full_result())["llm_audit"],
            [
                {"node": "quality", "provider": "xiaomi", "model": "mimo", "count": 12},
                {"node": "market_impact", "provider": "xiaomi", "model": "mimo", "count": 1},
            ],
        )

    def test_analyses_counts_use_enrichment_flag_not_analysis_method(self) -> None:
        # Every entry keeps analysis_method == "heuristic_draft", as in real runs.
        analyses = summarize_result(_full_result(failing=3, total=10))["analyses"]

        self.assertEqual(analyses["total"], 10)
        self.assertEqual(analyses["llm_failed"], 3)
        self.assertEqual(analyses["llm_ok"], 7)
        self.assertEqual(analyses["with_translated_title"], 7)
        self.assertEqual(len(analyses["llm_error_reasons"]), 1)
        self.assertEqual(analyses["llm_error_reasons"][0]["count"], 3)

    def test_check_mode_result_keeps_the_check_decision(self) -> None:
        check = {"action": "skip", "reason": "already fetched today"}
        summary = summarize_result({"run_id": "r", "mode": "check", "check": check})

        self.assertEqual(summary["check"], check)
        self.assertEqual(summary["analyses"]["total"], 0)

    def test_missing_optional_sections_do_not_break(self) -> None:
        summary = summarize_result({"run_id": "r", "mode": "deliver"})

        self.assertEqual(summary["errors"]["total"], 0)
        self.assertEqual(summary["llm_audit"], [])
        self.assertNotIn("run_summary", summary)
        self.assertNotIn("full_result_path", summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
