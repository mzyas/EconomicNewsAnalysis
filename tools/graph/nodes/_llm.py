"""Hermes-managed LLM and research-adapter construction shared by nodes."""

from __future__ import annotations

from typing import Any

from ..llm_runtime import get_run_hermes_llm
from ..research import ResearchAdapter


def _resolve_hermes_llm(graph_cfg: dict[str, Any], state: dict[str, Any]) -> Any | None:
    """Resolve the non-serializable Hermes facade for this graph run."""
    return graph_cfg.get("_hermes_llm") or get_run_hermes_llm(state.get("run_id"))


def _new_research_adapter(
    graph_cfg: dict[str, Any],
    hermes_llm: Any | None,
    llm_audit: list[dict[str, str]],
    temperature: float | None = None,
) -> ResearchAdapter:
    """Create a graph adapter without touching provider SDKs or credentials."""
    adapter = ResearchAdapter(
        temperature=float(
            graph_cfg.get("temperature", 0.2) if temperature is None else temperature
        ),
        hermes_llm=hermes_llm,
        model_routes=dict(graph_cfg.get("model_routes") or {}),
        strict_model_routes=bool(graph_cfg.get("strict_model_routes", True)),
        llm_audit=llm_audit,
    )
    fake_llm = graph_cfg.get("_fake_llm")
    if fake_llm is not None:
        adapter.set_fake_llm(fake_llm)
    return adapter
