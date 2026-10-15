"""SQLite checkpoint manager for graph persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

_DEFAULT_THREAD_NS = ""


def create_checkpoint_saver(checkpoint_path: str) -> SqliteSaver:
    """Create a ``SqliteSaver`` whose parent directory is ensured.

    The underlying ``sqlite3.Connection`` is kept open for the lifetime of
    the graph run; callers are responsible for closing it (typically via
    ``saver.conn.close()``) once execution completes.
    """

    path = Path(checkpoint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def _build_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": _DEFAULT_THREAD_NS}}


def load_latest_state(checkpoint_path: str, thread_id: str) -> dict[str, Any] | None:
    """Return the most recent checkpointed state for ``thread_id``."""

    saver = create_checkpoint_saver(checkpoint_path)
    try:
        snapshot = saver.get_tuple(_build_config(thread_id))
    finally:
        saver.conn.close()
    if snapshot is None:
        return None
    payload = snapshot.checkpoint.get("channel_values")
    if not isinstance(payload, dict):
        return None
    return dict(payload)


# Tables written by ``SqliteSaver``; every row is keyed by ``thread_id``.
_CHECKPOINT_TABLES = ("writes", "checkpoints")


def prune_checkpoints(
    checkpoint_path: str,
    keep_last: int,
    *,
    vacuum: bool = True,
) -> dict[str, Any]:
    """Bound checkpoint-DB growth by keeping only the most recent threads.

    Each workflow run is one ``thread_id`` and accumulates several checkpoint +
    write rows that are never reclaimed, so the SQLite file grows without limit
    (observed at ~770 MB / 357 threads before this was added). We keep the
    newest ``keep_last`` threads — enough to ``resume`` a recent run — and delete
    the rest. Thread recency is ordered by ``MAX(checkpoint_id)``: LangGraph
    checkpoint ids are time-sortable (uuid6), so the lexicographic max is the
    latest checkpoint for that thread.

    Returns a summary dict ``{deleted_threads, kept_threads, ...}``. A
    ``keep_last <= 0`` or a missing DB file is a no-op. Safe to call after the
    run's own saver has been closed.
    """
    summary: dict[str, Any] = {
        "deleted_threads": 0,
        "kept_threads": 0,
        "deleted_rows": 0,
        "vacuumed": False,
    }
    if keep_last <= 0 or not Path(checkpoint_path).exists():
        return summary

    conn = sqlite3.connect(checkpoint_path)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "checkpoints" not in existing:
            return summary

        # Newest thread first; keep the head, delete the tail.
        threads = [
            row[0]
            for row in conn.execute(
                "SELECT thread_id FROM checkpoints "
                "GROUP BY thread_id ORDER BY MAX(checkpoint_id) DESC"
            )
        ]
        summary["kept_threads"] = min(len(threads), keep_last)
        stale = threads[keep_last:]
        if not stale:
            return summary

        placeholders = ",".join("?" * len(stale))
        deleted_rows = 0
        for table in _CHECKPOINT_TABLES:
            if table not in existing:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE thread_id IN ({placeholders})",
                stale,
            )
            deleted_rows += cur.rowcount or 0
        conn.commit()
        summary["deleted_threads"] = len(stale)
        summary["deleted_rows"] = deleted_rows

        if vacuum:
            conn.execute("VACUUM")
            summary["vacuumed"] = True
    finally:
        conn.close()
    return summary
