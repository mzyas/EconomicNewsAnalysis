"""Tests for the LangGraph state, checkpoint and event modules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import Checkpoint

from tools.graph.checkpoint import create_checkpoint_saver, load_latest_state
from tools.graph.events import record_event
from tools.graph.state import NewsAnalysisState, initial_state


def _sample_config() -> dict[str, Any]:
    return {
        "mode": "briefing",
        "output_language": "zh-CN",
        "max_ranked_items": 20,
        "graph": {
            "checkpoint_path": ".hermes/graph_checkpoints",
        },
    }


def _write_checkpoint(
    saver: Any,
    thread_id: str,
    state: dict[str, Any],
    checkpoint_id: str,
) -> None:
    checkpoint = Checkpoint(
        v=1,
        id=checkpoint_id,
        ts="2026-01-01T00:00:00+00:00",
        channel_values=dict(state),
        channel_versions={},
        versions_seen={},
    )
    saver.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        checkpoint,
        {"step": 1},
        {},
    )


class InitialStateTests(unittest.TestCase):
    def test_generates_unique_run_id_by_default(self) -> None:
        first = initial_state(_sample_config())
        second = initial_state(_sample_config())
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(first["run_id"]), 36)
        self.assertEqual(first["run_id"].count("-"), 4)

    def test_accepts_explicit_run_id_for_resume(self) -> None:
        state = initial_state(_sample_config(), run_id="resume-1234")
        self.assertEqual(state["run_id"], "resume-1234")

    def test_preserves_mode_and_config(self) -> None:
        config = _sample_config()
        state = initial_state(config, stdin_text="hello")
        self.assertEqual(state["mode"], "briefing")
        self.assertEqual(state["config"], config)
        self.assertEqual(state["stdin_text"], "hello")
        self.assertEqual(state["pipeline_status"], "running")
        self.assertNotIn("status", state)

    def test_exposes_required_public_fields(self) -> None:
        state = initial_state(_sample_config())
        public_keys = {
            "run_id",
            "mode",
            "config",
            "stdin_text",
            "items",
            "error_items",
            "ranked_items",
            "analysis",
            "briefing",
            "evidence",
            "mainlines",
            "daily",
            "delivery",
            "meta",
            "output_dir",
            "errors",
            "warnings",
            "started_at",
        }
        self.assertTrue(public_keys.issubset(state.keys()))

    def test_exposes_required_internal_fields(self) -> None:
        state = initial_state(_sample_config())
        internal_keys = {
            "_phase",
            "_evidence",
            "_quality_gate",
            "_retry_count",
            "_delivery_approved",
            "_events",
            "_research_queue",
        }
        self.assertTrue(internal_keys.issubset(state.keys()))
        self.assertEqual(state["_phase"], "init")
        self.assertEqual(state["_retry_count"], 0)
        self.assertEqual(state["_delivery_approved"], False)
        self.assertEqual(state["_events"], [])
        self.assertEqual(state["_research_queue"], [])

    def test_internal_fields_are_excluded_from_public_projection(self) -> None:
        state = initial_state(_sample_config())
        public = {key: value for key, value in state.items() if not key.startswith("_")}
        for key in state:
            if key.startswith("_"):
                self.assertNotIn(key, public)
        self.assertIn("run_id", public)
        self.assertIn("mode", public)

    def test_state_keys_typed_dict_declares(self) -> None:
        self.assertIn("run_id", NewsAnalysisState.__optional_keys__)
        self.assertIn("_phase", NewsAnalysisState.__optional_keys__)


class RecordEventTests(unittest.TestCase):
    def test_returns_updated_events_list(self) -> None:
        state = initial_state(_sample_config())
        events = record_event(state, "fetch", "completed")
        self.assertIs(events, state["_events"])
        self.assertEqual(len(events), 1)

    def test_event_contains_required_fields(self) -> None:
        state = initial_state(_sample_config())
        events = record_event(state, "analyze", "completed", error_count=2)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["run_id"], state["run_id"])
        self.assertEqual(event["node"], "analyze")
        self.assertEqual(event["status"], "completed")
        self.assertEqual(event["error_count"], 2)
        self.assertIn("started_at", event)
        self.assertIn("ended_at", event)

    def test_appends_to_existing_events(self) -> None:
        state = initial_state(_sample_config())
        record_event(state, "fetch", "completed")
        events = record_event(state, "rank", "failed", error_count=3)
        self.assertEqual(len(events), 2)
        self.assertEqual([event["node"] for event in events], ["fetch", "rank"])
        self.assertEqual(events[1]["error_count"], 3)

    def test_internal_events_excluded_from_public_projection(self) -> None:
        state = initial_state(_sample_config())
        record_event(state, "fetch", "completed")
        public = {key: value for key, value in state.items() if not key.startswith("_")}
        self.assertNotIn("events", public)


class CheckpointTests(unittest.TestCase):
    def test_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = Path(temp_dir) / "sub" / "deep" / "checkpoints.sqlite"
            self.assertFalse(nested.parent.exists())
            saver = create_checkpoint_saver(str(nested))
            try:
                self.assertTrue(nested.parent.exists())
                self.assertTrue(nested.exists())
            finally:
                saver.conn.close()

    def test_round_trip_with_same_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoints.sqlite"
            thread_id = "run-abc"
            state = {
                "run_id": thread_id,
                "mode": "briefing",
                "config": _sample_config(),
                "items": [{"id": "i-1"}],
                "_phase": "fetched",
            }
            saver = create_checkpoint_saver(str(path))
            try:
                _write_checkpoint(saver, thread_id, state, "cp-1")
            finally:
                saver.conn.close()
            loaded = load_latest_state(str(path), thread_id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["run_id"], thread_id)
            self.assertEqual(loaded["mode"], "briefing")
            self.assertEqual(loaded["items"], [{"id": "i-1"}])

    def test_different_thread_ids_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoints.sqlite"
            first_state = {"run_id": "run-a", "mode": "analyze", "items": []}
            second_state = {"run_id": "run-b", "mode": "deliver", "items": []}
            saver = create_checkpoint_saver(str(path))
            try:
                _write_checkpoint(saver, "run-a", first_state, "cp-a")
                _write_checkpoint(saver, "run-b", second_state, "cp-b")
            finally:
                saver.conn.close()
            first_loaded = load_latest_state(str(path), "run-a")
            second_loaded = load_latest_state(str(path), "run-b")
            self.assertIsNotNone(first_loaded)
            self.assertIsNotNone(second_loaded)
            assert first_loaded is not None
            assert second_loaded is not None
            self.assertEqual(first_loaded["mode"], "analyze")
            self.assertEqual(second_loaded["mode"], "deliver")

    def test_load_latest_state_returns_none_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "checkpoints.sqlite"
            saver = create_checkpoint_saver(str(path))
            saver.conn.close()
            self.assertIsNone(load_latest_state(str(path), "unknown-thread"))


if __name__ == "__main__":
    unittest.main()
