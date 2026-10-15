#!/usr/bin/env python3
"""Unified Runtime Skill entrypoint.

This is a thin facade over the LangGraph workflow defined in
:mod:`tools.graph.workflow`. CLI arguments are kept fully backward
compatible with the legacy runtime and two new flags are added:
``--run-id`` to thread a custom identifier through the run, and
``--resume`` to load a checkpoint instead of starting fresh.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.config_loader import ROOT, load_runtime_config
from tools.graph.workflow import run_workflow
from tools.schema_validation import validate_file


MODES = {"analyze", "fetch", "briefing", "deliver", "check", "list-sources", "trend"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated(result: dict[str, Any]) -> dict[str, Any]:
    validate_file(result, ROOT / "schemas" / "runtime_result.schema.json")
    return result


def _project_to_old_schema(state: dict[str, Any]) -> dict[str, Any]:
    """Filter a graph state to the public runtime_result schema.

    The internal graph state carries private channels prefixed with ``_``
    (e.g. ``_phase``, ``_evidence``, ``_retry_count``, ``_quality_gate``,
    ``_events``) plus runtime bookkeeping such as ``delivery`` that should
    never leak into the public payload. This helper keeps only the keys
    declared in ``schemas/runtime_result.schema.json`` and drops anything
    starting with ``_``.

    The returned dict is safe to validate against
    ``schemas/runtime_result.schema.json``; the caller is responsible for
    ensuring all required fields are present.
    """
    schema_path = ROOT / "schemas" / "runtime_result.schema.json"
    allowed = set(
        json.loads(schema_path.read_text(encoding="utf-8"))
        .get("properties", {})
        .keys()
    )
    return {
        key: value
        for key, value in state.items()
        if not key.startswith("_") and key in allowed
    }


def execute(
    config: dict[str, Any],
    stdin_text: str | None = None,
    *,
    run_id: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Public entrypoint used by both the CLI and other tools."""
    config = dict(config)
    if stdin_text is not None:
        config["stdin_text"] = stdin_text
    try:
        result = run_workflow(
            config,
            mode=config.get("mode"),
            run_id=run_id,
            resume=resume,
        )
    except Exception as exc:
        skill_identity = config.get("_skill") or {
            "name": "economic-news-analysis",
            "version": "unknown",
        }
        mode = config.get("mode", "analyze")
        error = {
            "run_id": run_id or str(uuid.uuid4()),
            "skill": skill_identity,
            "mode": mode,
            "status": "failed",
            "pipeline_status": "failed",
            "output_status": "skipped",
            "delivery_status": "skipped",
            "delivery_kind": "none",
            "started_at": _now(),
            "completed_at": _now(),
            "output_language": config.get("output_language", "zh-CN"),
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
            "errors": [{"type": type(exc).__name__, "message": str(exc)}],
            "warnings": [],
            "market_context": {
                "enabled": bool(config.get("market_data", {}).get("enabled", False)),
                "provider": config.get("market_data", {}).get("provider"),
                "status": "skipped",
                "snapshots": [],
                "errors": [],
            },
            "output_files": {},
        }
        validate_file(error, ROOT / "schemas" / "runtime_result.schema.json")
        raise
    result = _project_to_old_schema(result)
    return _validated(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Economic News Analysis Runtime Skill")
    parser.add_argument("--config", type=Path, help="Runtime YAML/JSON config")
    parser.add_argument("--mode", choices=sorted(MODES), help="Override runtime mode")
    parser.add_argument("--output-dir", type=Path, help="Override artifact directory")
    parser.add_argument("--stdin", action="store_true", help="Read one article from stdin")
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        help="Console output format override",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        help="Use a custom run identifier (also drives checkpoint thread_id).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a previous run from its checkpoint (requires --run-id).",
    )
    return parser


def _failure_payload(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    try:
        skill_identity = load_runtime_config()["_skill"]
    except Exception:
        skill_identity = {
            "name": "economic-news-analysis",
            "version": "unknown",
        }
    return {
        "run_id": str(uuid.uuid4()),
        "skill": skill_identity,
        "mode": args.mode or "analyze",
        "status": "failed",
        "pipeline_status": "failed",
        "output_status": "skipped",
        "delivery_status": "skipped",
        "delivery_kind": "none",
        "started_at": _now(),
        "completed_at": _now(),
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
        "errors": [{"type": type(exc).__name__, "message": str(exc)}],
        "warnings": [],
        "market_context": {
            "enabled": False,
            "provider": None,
            "status": "skipped",
            "snapshots": [],
            "errors": [],
        },
        "output_files": {},
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        config = load_runtime_config(args.config)
        # Fix CWD so relative paths (checkpoint_path, etc.) resolve correctly
        os.chdir(ROOT)
        if args.mode:
            config["mode"] = args.mode
        if args.output_dir:
            config["output_dir"] = str(args.output_dir)
        if args.format:
            config["output_format"] = args.format
        stdin_text = sys.stdin.read() if args.stdin else None
        result = execute(
            config,
            stdin_text,
            run_id=args.run_id,
            resume=args.resume,
        )
    except SystemExit:
        raise
    except Exception as exc:
        try:
            error = _failure_payload(args, exc)
            validate_file(error, ROOT / "schemas" / "runtime_result.schema.json")
            print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        except Exception:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    if config.get("output_format") == "markdown" and (
        result.get("briefing_markdown") or result.get("analysis_markdown")
    ):
        print(result.get("briefing_markdown") or result["analysis_markdown"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
