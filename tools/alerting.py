"""Failure alerting for scheduled / unattended runs.

A scheduled briefing or smoke run that fails silently is the original "跑不通"
pain point: the error only ever reaches stderr, which nobody watching a cron
job ever reads. This module turns a failure into something a human (or a
scheduler) can actually notice:

1. A structured entry is appended to ``logs/alerts.jsonl`` (durable history).
2. A prominent ``ALERT`` banner is printed to stderr.
3. If the ``ECON_NEWS_ALERT_CMD`` env var names a command, it is invoked with
   the one-line summary on stdin and as a trailing argument. This is the
   escape hatch that lets a scheduler wire any channel — a himalaya send, a
   webhook ``curl``, a desktop notifier — without this skill hard-coding one.

The caller is still responsible for the process exit code (smoke / runtime
already exit non-zero on failure); alerting is the *notification* half of the
"被调度器捕获的退出码 / 失败时通知" gap.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.run_log import append_log

# Env var holding a command template invoked on failure. Split with shlex so
# ``ECON_NEWS_ALERT_CMD="curl -X POST https://hooks.example/notify -d @-"`` works.
ALERT_CMD_ENV = "ECON_NEWS_ALERT_CMD"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_notify_command(command: str, summary: str) -> dict[str, Any]:
    """Best-effort invoke the user-configured notifier; never raise."""
    result: dict[str, Any] = {"command": command, "invoked": False}
    try:
        argv = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as exc:
        result["error"] = f"unparseable {ALERT_CMD_ENV}: {exc}"
        return result
    if not argv:
        return result
    try:
        completed = subprocess.run(
            argv + [summary],
            input=summary,
            text=True,
            capture_output=True,
            timeout=30,
        )
        result["invoked"] = True
        result["returncode"] = completed.returncode
        if completed.returncode != 0:
            result["error"] = (completed.stderr or "").strip()[:500]
    except Exception as exc:  # noqa: BLE001 - alerting must not crash the caller
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def send_failure_alert(
    *,
    source: str,
    summary: str,
    details: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Record and broadcast a failure.

    Args:
        source: where the failure originated, e.g. ``"workflow_smoke"``.
        summary: one-line human-readable description (used for the notifier).
        details: optional structured context (failed ids, errors, …).
        log_path: alerts JSONL sink; defaults to ``logs/alerts.jsonl``.

    Returns the recorded alert entry (including any notifier outcome). Never
    raises — alerting failures must not mask the underlying failure.
    """
    if log_path is None:
        log_path = Path(__file__).resolve().parent.parent / "logs" / "alerts.jsonl"

    entry: dict[str, Any] = {
        "ts": _now(),
        "source": source,
        "summary": summary,
        "details": details or {},
    }

    command = os.environ.get(ALERT_CMD_ENV, "").strip()
    if command:
        entry["notify"] = _run_notify_command(command, f"[{source}] {summary}")

    try:
        append_log(log_path, entry)
        entry["logged_to"] = str(log_path)
    except Exception as exc:  # noqa: BLE001
        entry["log_error"] = f"{type(exc).__name__}: {exc}"

    print(f"\n!! ALERT [{source}]: {summary}", file=sys.stderr)
    notify = entry.get("notify")
    if notify and notify.get("error"):
        print(f"!! ALERT notifier failed: {notify['error']}", file=sys.stderr)
    elif command and notify and notify.get("invoked"):
        print(f"!! ALERT dispatched via {ALERT_CMD_ENV}", file=sys.stderr)
    elif not command:
        print(
            f"!! (set {ALERT_CMD_ENV} to forward alerts to email/webhook/notifier)",
            file=sys.stderr,
        )
    return entry
