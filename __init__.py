"""Native Hermes plugin entry for the economic-news workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SKILL_DESCRIPTION = (
    "Run the economic-news LangGraph workflow for supplied text or a scheduled briefing."
)


def run_langgraph_workflow(
    ctx: Any,
    source_text: str = "",
    *,
    run_mode: str = "analyze",
    profile_config: str | None = None,
) -> dict[str, Any]:
    """Load graph dependencies only when the Hermes tool is invoked."""
    if __package__:
        from .tools.hermes_plugin import run_langgraph_workflow as run_workflow
    else:  # pragma: no cover - direct local import compatibility
        from tools.hermes_plugin import run_langgraph_workflow as run_workflow
    return run_workflow(
        ctx,
        source_text,
        run_mode=run_mode,
        profile_config=profile_config,
    )


def _tool_result(result: dict[str, Any], run_mode: str) -> dict[str, Any]:
    """Scheduled modes return a compact summary; the full result is in the output JSON."""
    if __package__:
        from .tools.result_summary import SUMMARY_MODES, summarize_result
    else:  # pragma: no cover - direct local import compatibility
        from tools.result_summary import SUMMARY_MODES, summarize_result
    return summarize_result(result) if run_mode in SUMMARY_MODES else result


_RUN_LANGGRAPH_WORKFLOW_DESCRIPTION = (
    "Analyze economic news through the LangGraph workflow using "
    "Hermes-managed LLM routing. Daily delivery uses a fixed profile."
)

# Hermes expects a tool schema of the form {"description", "parameters": <JSON Schema>};
# a bare JSON Schema here leaves the tool with no declared parameters.
_RUN_LANGGRAPH_WORKFLOW_SCHEMA = {
    "description": _RUN_LANGGRAPH_WORKFLOW_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "source_text": {
                "type": "string",
                "description": "Article or economic-news text to analyze. Required except for daily_email delivery.",
            },
            "run_mode": {
                "type": "string",
                "enum": ["briefing", "deliver", "analyze", "fetch", "check", "trend"],
                "description": "Existing workflow mode. Defaults to analyze.",
            },
            "profile_config": {
                "type": "string",
                "enum": ["daily_email"],
                "description": "Fixed profile for scheduled daily email delivery. No file paths are accepted.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _make_workflow_handler(ctx: Any):
    """Bind Hermes' per-plugin context to the model-tool handler."""

    def handler(args: dict[str, Any], **_kwargs: Any) -> str:
        if not isinstance(args, dict):
            raise ValueError("run_langgraph_workflow arguments must be an object")
        source_text = args.get("source_text", "")
        if not isinstance(source_text, str):
            raise ValueError("source_text must be a string")
        run_mode = args.get("run_mode", "analyze")
        if not isinstance(run_mode, str):
            raise ValueError("run_mode must be a string")
        profile_config = args.get("profile_config")
        if profile_config is not None and not isinstance(profile_config, str):
            raise ValueError("profile_config must be a string")
        if run_mode == "deliver" and profile_config != "daily_email":
            raise ValueError("run_mode 'deliver' requires profile_config 'daily_email'")
        if run_mode != "deliver" and profile_config is not None:
            raise ValueError("profile_config is only supported with run_mode 'deliver'")
        if not source_text.strip() and profile_config != "daily_email":
            raise ValueError("source_text must be a non-empty string")
        workflow_args: dict[str, Any] = {"run_mode": run_mode}
        if profile_config is not None:
            workflow_args["profile_config"] = profile_config
        result = run_langgraph_workflow(ctx, source_text, **workflow_args)
        return json.dumps(_tool_result(result, run_mode), ensure_ascii=False)

    return handler


def register(ctx: Any) -> None:
    """Expose the compiled LangGraph workflow as one Hermes tool."""
    ctx.register_tool(
        name="run_langgraph_workflow",
        toolset="EconomicNewsAnalysis",
        schema=_RUN_LANGGRAPH_WORKFLOW_SCHEMA,
        handler=_make_workflow_handler(ctx),
        description=_RUN_LANGGRAPH_WORKFLOW_DESCRIPTION,
        emoji="📰",
    )
    _register_skill(ctx)


def _register_skill(ctx: Any) -> None:
    """Expose the bundled SKILL.md as ``EconomicNewsAnalysis:EconomicNewsAnalysis``.

    Hermes does not discover skills inside a plugin directory on its own; they
    must be registered. Hosts without ``register_skill`` simply skip it.
    """
    register_skill = getattr(ctx, "register_skill", None)
    if register_skill is None:
        return
    register_skill(
        "EconomicNewsAnalysis",
        Path(__file__).resolve().parent / "skills" / "EconomicNewsAnalysis" / "SKILL.md",
        description=_SKILL_DESCRIPTION,
    )
