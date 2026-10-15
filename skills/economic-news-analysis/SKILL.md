---
name: economic-news-analysis
description: Run the economic-news LangGraph workflow for supplied text or a scheduled briefing.
allowed-tools: run_langgraph_workflow
---

# Economic News Analysis

Use `run_langgraph_workflow` for the requested workflow mode. Provide the
source text for an article analysis, then read the returned `llm_audit` to
confirm which Hermes-managed route actually served each graph node.

Do not select a provider or model in this skill. The Cron job selects its main
model, and any node-specific routes are supplied by the Hermes plugin profile.
