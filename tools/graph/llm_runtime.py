"""Run-scoped access to Hermes' non-serializable LLM completion facade."""

from __future__ import annotations

from typing import Any


_RUN_LLMS: dict[str, Any] = {}


def set_run_hermes_llm(run_id: str, llm: Any) -> None:
    """Register ``ctx.llm`` for one graph run without checkpointing it."""
    _RUN_LLMS[run_id] = llm


def get_run_hermes_llm(run_id: str | None) -> Any | None:
    """Return the facade registered for this graph run, if any."""
    return _RUN_LLMS.get(str(run_id or ""))


def clear_run_hermes_llm(run_id: str) -> None:
    """Release the run-scoped facade after graph execution."""
    _RUN_LLMS.pop(run_id, None)
