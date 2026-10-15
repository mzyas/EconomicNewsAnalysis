"""Tests for serializable run-budget checkpoints."""

from __future__ import annotations

import unittest

from tools.graph.run_budget import (
    begin_stage,
    complete_stage,
    create_run_budget,
    should_stop_optional_work,
)


class RunBudgetTests(unittest.TestCase):
    def test_default_budget_reserves_finalization_time(self) -> None:
        budget = create_run_budget(600, now=100.0)

        self.assertEqual(budget["total_seconds"], 600)
        self.assertEqual(budget["reserve_seconds"], 60)
        self.assertEqual(budget["deadline_monotonic"], 700.0)
        self.assertEqual(budget["finalize_deadline_monotonic"], 640.0)
        self.assertFalse(budget["degraded"])

    def test_checkpoint_records_stage_progress_and_duration(self) -> None:
        budget = create_run_budget(600, now=100.0)
        begin_stage(
            budget,
            "evidence",
            completed_count=1,
            target_count=3,
            active_item="https://publisher.example/article",
            now=110.0,
        )
        complete_stage(budget, "evidence", completed_count=2, now=118.5)

        checkpoint = budget["checkpoints"][-1]
        self.assertEqual(checkpoint["stage"], "evidence")
        self.assertEqual(checkpoint["status"], "completed")
        self.assertEqual(checkpoint["completed_count"], 2)
        self.assertEqual(checkpoint["target_count"], 3)
        self.assertEqual(checkpoint["active_item"], "https://publisher.example/article")
        self.assertEqual(checkpoint["duration_seconds"], 8.5)
        self.assertEqual(checkpoint["remaining_seconds"], 581.5)

    def test_finalization_window_marks_optional_work_degraded(self) -> None:
        budget = create_run_budget(600, now=100.0)
        begin_stage(
            budget,
            "quality",
            completed_count=0,
            target_count=2,
            active_item="retry 1",
            now=641.0,
        )

        self.assertTrue(should_stop_optional_work(budget, now=641.0))
        self.assertTrue(budget["degraded"])
        self.assertEqual(budget["degradation_reason"], "finalization_reserve")
        self.assertEqual(budget["blocked_stage"], "quality")
        self.assertEqual(budget["blocked_item"], "retry 1")


if __name__ == "__main__":
    unittest.main()
