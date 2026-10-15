#!/usr/bin/env python3
"""Workflow smoke-test harness with failure logging.

Runs every workflow declared in ``workflows.yaml`` through its example config
and records one JSONL entry per run to ``logs/workflow_smoke.jsonl``. When a
workflow cannot run through (an exception, ``status: failed`` or a schema
error) the entry carries the error type, message and full traceback so the
failure is diagnosable after the fact instead of vanishing to stderr.

By default it runs **offline**: RSS fetch, market data and email sending are
disabled and the LLM is stubbed, so the run is fast, deterministic and free of
network / API cost. Pass ``--live`` to exercise the real fetch + LLM (Windows
Credential Manager ``deepseek-key``) + market-data path; email sending stays
disabled in both modes so the smoke test never delivers mail.

Usage::

    uv run python tools/workflow_smoke.py            # offline, all workflows
    uv run python tools/workflow_smoke.py --live      # real fetch + LLM
    uv run python tools/workflow_smoke.py --only daily-email-briefing
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.alerting import send_failure_alert
from tools.config_loader import ROOT, _load_mapping, load_runtime_config
from tools.graph import research
from tools.graph.workflow import run_workflow
from tools.market_data.providers.fake_provider import FakeMarketProvider
from tools.market_data.registry import register_market_data_provider
from tools.run_log import append_log

# Modes that rank/analyse with content; the offline run injects synthetic news
# items for these so the analyze -> quality_gate -> build_briefing path runs.
_CONTENT_MODES = {"briefing", "deliver", "analyze"}

_STUB_ITEMS = [
    {
        "title": "Federal Reserve keeps rates unchanged as inflation stays elevated",
        "summary": "Officials said future decisions remain data dependent.",
        "source_id": "fed_fomc",
        "source_name": "Federal Reserve",
        "source_country": "US",
        "source_language": "en",
        "source_tags": ["monetary_policy", "inflation", "usd"],
    },
    {
        "title": "中国人民银行：5月金融市场运行平稳",
        "summary": "货币市场利率小幅上行，债券融资结构有所变化。",
        "source_id": "pbc_news",
        "source_name": "中国人民银行",
        "source_country": "CN",
        "source_language": "zh",
        "source_tags": ["monetary_policy", "cny"],
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stub_invoke(self: Any, prompt: str, *, node: str = "analysis") -> str:  # noqa: ARG001 - signature match
    """Deterministic offline LLM stand-in returning canned valid JSON.

    Branches on the prompt so the market-impact synthesis gets its 5-key map
    and every other call (analysis / enrichment) gets a translated, signal-rich
    payload that satisfies the strict quality gate.
    """
    if "中美日宏观背景" in prompt:
        return json.dumps(
            {
                "CN": "（冒烟测试）中国货币与财政政策维持稳健，社融与信贷边际改善，地产仍是主要拖累。",
                "US": "（冒烟测试）美国通胀回落但仍高于目标，美联储维持观望，劳动力市场边际降温。",
                "JP": "（冒烟测试）日本通胀温和，日银渐进退出超宽松，关注薪资增长与日元走势。",
            },
            ensure_ascii=False,
        )
    if "市场影响地图" in prompt:
        return json.dumps(
            {
                "rates_bonds": "利率维持高位，债市窄幅震荡。",
                "fx": "美元偏强，非美货币承压。",
                "equities": "股指估值受融资成本压制。",
                "commodities": "金价区间整理，原油受需求预期扰动。",
                "risk_appetite": "风险偏好边际转弱，仅说明非个性化暴露变化。",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "translated_title": "（冒烟测试）美联储维持利率不变",
            "translated_summary": "（冒烟测试）央行维持利率，后续取决于数据。",
            "signals": [
                {
                    "signal": "利率维持不变，政策保持观望",
                    "type": "事实",
                    "impact": "短端利率支撑有限",
                    "assets": ["美元", "美国国债"],
                }
            ],
            "focus_assets": ["美元", "美国国债"],
        },
        ensure_ascii=False,
    )


def _offline_config(config: dict[str, Any], mode: str, output_dir: str) -> dict[str, Any]:
    """Apply safe offline overrides: no network / LLM / email side effects."""
    config = dict(config)
    config["fetch_enabled"] = False
    config["output_dir"] = output_dir
    market = dict(config.get("market_data") or {})
    if mode == "trend":
        # Trend needs market data to produce content. Keep it enabled but swap
        # in the deterministic fake provider (registered in main) so the offline
        # run is fast, network-free and reproducible.
        market["enabled"] = True
        market["provider"] = "fake"
    else:
        market["enabled"] = False
    config["market_data"] = market
    email = dict(config.get("email") or {})
    email["send"] = False
    email.setdefault("from", "smoke@example.com")
    email.setdefault("to", "smoke@example.com")
    config["email"] = email
    if mode in _CONTENT_MODES and not config.get("input_items"):
        config["input_items"] = [dict(item) for item in _STUB_ITEMS]
    return config


def _live_config(config: dict[str, Any], output_dir: str) -> dict[str, Any]:
    """Real fetch + LLM + market data, but never actually send email."""
    config = dict(config)
    config["output_dir"] = output_dir
    email = dict(config.get("email") or {})
    email["send"] = False
    config["email"] = email
    return config


def run_one(workflow_id: str, workflow: dict[str, Any], live: bool, output_dir: str) -> dict[str, Any]:
    """Run a single workflow and return its JSONL log entry."""
    mode = str(workflow.get("mode", "briefing"))
    example_config = str(workflow.get("example_config", ""))
    run_id = f"smoke-{workflow_id}-{uuid.uuid4().hex[:8]}"
    entry: dict[str, Any] = {
        "ts": _now(),
        "run_id": run_id,
        "workflow": workflow_id,
        "mode": mode,
        "example_config": example_config,
        "live": live,
        "outcome": "fail",
        "status": None,
        "duration_s": None,
        "stats": None,
        "errors": None,
        "error_type": None,
        "error_message": None,
        "traceback": None,
    }

    started = datetime.now(timezone.utc)
    try:
        config = load_runtime_config(ROOT / example_config)
        config = _live_config(config, output_dir) if live else _offline_config(config, mode, output_dir)
        result = run_workflow(config, mode=mode, run_id=run_id)
        entry["status"] = result.get("status")
        entry["stats"] = result.get("statistics")
        errors = result.get("errors") or []
        entry["errors"] = errors
        if entry["status"] == "failed":
            entry["outcome"] = "fail"
            entry["error_type"] = "WorkflowFailed"
            entry["error_message"] = "; ".join(
                str(e.get("message", e)) for e in errors
            ) or "status=failed"
        else:
            entry["outcome"] = "pass"
    except Exception as exc:  # noqa: BLE001 - smoke test records every failure
        entry["outcome"] = "fail"
        entry["error_type"] = type(exc).__name__
        entry["error_message"] = str(exc)
        entry["traceback"] = traceback.format_exc()
    finally:
        entry["duration_s"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 2
        )
    return entry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow smoke-test harness")
    parser.add_argument("--live", action="store_true", help="Real fetch + LLM + market data (email still disabled)")
    parser.add_argument("--only", action="append", default=[], metavar="WORKFLOW_ID", help="Run only these workflow ids (repeatable)")
    parser.add_argument("--log-path", type=Path, default=ROOT / "logs" / "workflow_smoke.jsonl", help="JSONL log destination")
    parser.add_argument("--alert-on-fail", action="store_true", help="On any failure, record an alert and fire ECON_NEWS_ALERT_CMD if set")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_parser().parse_args()
    registry = _load_mapping(ROOT / "workflows.yaml")
    workflows = registry.get("workflows", {})
    selected = {k: v for k, v in workflows.items() if not args.only or k in set(args.only)}
    if not selected:
        print(f"No matching workflows (only={args.only}); available: {list(workflows)}", file=sys.stderr)
        return 2

    output_dir = tempfile.mkdtemp(prefix="workflow-smoke-")

    # Offline runs use deterministic stand-ins: a stubbed LLM and a fake market
    # provider (selected via market_data.provider="fake" in _offline_config).
    # Restore the LLM afterwards.
    original_invoke = research.ResearchAdapter._invoke
    if not args.live:
        research.ResearchAdapter._invoke = _stub_invoke
        register_market_data_provider("fake", FakeMarketProvider)

    failures = 0
    failed_ids: list[str] = []
    try:
        for workflow_id, workflow in selected.items():
            entry = run_one(workflow_id, workflow, args.live, output_dir)
            append_log(args.log_path, entry)
            marker = "PASS" if entry["outcome"] == "pass" else "FAIL"
            detail = f" status={entry['status']}" if entry["outcome"] == "pass" else f" {entry['error_type']}: {entry['error_message']}"
            print(f"[{marker}] {workflow_id} ({entry['mode']}, {entry['duration_s']}s){detail}", file=sys.stderr)
            if entry["outcome"] != "pass":
                failures += 1
                failed_ids.append(workflow_id)
    finally:
        research.ResearchAdapter._invoke = original_invoke

    if failures and args.alert_on_fail:
        send_failure_alert(
            source="workflow_smoke",
            summary=f"{failures}/{len(selected)} workflows failed: {', '.join(failed_ids)}",
            details={"failed": failed_ids, "live": args.live, "log_path": str(args.log_path)},
        )

    total = len(selected)
    print(
        f"\n{total - failures}/{total} workflows passed "
        f"({'live' if args.live else 'offline'}); log -> {args.log_path}",
        file=sys.stderr,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
