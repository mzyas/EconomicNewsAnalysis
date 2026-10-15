"""Runtime JSONL logging and fetch-window checks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_log(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_last_entry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = json.loads(line)
    return last


def evaluate_fetch_window(
    path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    last = read_last_entry(path)
    if last is None:
        return {
            "action": "fetch",
            "reason": "no fetch log found",
            "last_fetch": None,
        }
    value = last.get("fetched_at") or last.get("started_at")
    try:
        last_fetch = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return {
            "action": "fetch",
            "reason": "invalid date in log",
            "last_fetch": value,
        }
    if last_fetch.tzinfo is None:
        last_fetch = last_fetch.replace(tzinfo=timezone.utc)
    same_day = last_fetch.astimezone(now.tzinfo).date() == now.date()
    return {
        "action": "skip" if same_day else "fetch",
        "reason": "already fetched today" if same_day else "new calendar day",
        "last_fetch": last_fetch.isoformat(),
        "gap_hours": round((now - last_fetch).total_seconds() / 3600, 1),
    }

