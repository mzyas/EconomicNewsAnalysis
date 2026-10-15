"""Native Hermes plugin entry for the economic-news workflow."""

from __future__ import annotations

import json
from typing import Any


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


_RUN_LANGGRAPH_WORKFLOW_SCHEMA = {
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
        return json.dumps(result, ensure_ascii=False)

    return handler


def register(ctx: Any) -> None:
    """Expose the compiled LangGraph workflow as one Hermes tool."""
    ctx.register_tool(
        name="run_langgraph_workflow",
        toolset="economic_news_analysis",
        schema=_RUN_LANGGRAPH_WORKFLOW_SCHEMA,
        handler=_make_workflow_handler(ctx),
        description=(
            "Analyze economic news through the LangGraph workflow using "
            "Hermes-managed LLM routing. Daily delivery uses a fixed profile."
        ),
        emoji="📰",
    )