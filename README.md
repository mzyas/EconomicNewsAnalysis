# EconomicNewsAnalysis

A [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline that fetches economic news,
builds a daily Chinese-language macro briefing (market trend table → 今日资讯主线 lead items →
📉 异动解读 news explaining large stock/index moves (only when something moved) →
🌏 官媒宏观背景 China/US/Japan macro background → 财经媒体 / 💡 科技与AI / 🔍 Google News /
🔥 热点速览 boards → market-impact map), and optionally emails it. It runs as a Hermes-consumed
skill but is fully usable from the CLI.

The graph stages are: **fetch** (RSS / World Bank / market data) → **mover news** (price moves
over a threshold trigger targeted Google News searches) → **normalize & dedupe** →
**rank** (per-section quotas + per-country balance) → **analyze** (Hermes-managed LLM, translation +
signals) → **quality gate** (strict; auto-enriches and synthesizes lead items, the China/US/Japan
macro background, and the impact map) → **build briefing** → **write outputs** →
**deliver** (himalaya email). Runs are checkpointed to SQLite so they can resume.

## Modes

Each mode maps to an entry in [`workflows.yaml`](workflows.yaml) with an example config:

| Mode      | Workflow id            | What it does                                                        | Example config                         |
|-----------|------------------------|---------------------------------------------------------------------|----------------------------------------|
| `briefing`| `daily-briefing`       | Full daily briefing saved to local files (no email)                 | `examples/daily_briefing.yaml`         |
| `deliver` | `daily-email-briefing` | Full daily briefing sent via email                                  | `daily_email_briefing.yaml`            |
| `analyze` | `news-analysis`        | Analyze user-supplied article(s) → structured signals               | `examples/single_article.yaml`         |
| `fetch`   | `fetch-only`           | Fetch, normalize, dedupe only — no LLM                              | `examples/fetch_only.yaml`             |
| `check`   | `source-check`         | Inspect fetch log / sources, decide whether to fetch this round     | `examples/source_check.yaml`           |
| `trend`   | `daily-trend`          | Deterministic market trend table only (no RSS / LLM)               | `examples/daily_trend.yaml`            |

## Setup & run

```bash
uv sync --group dev                                   # install (incl. pytest)
uv run python -m tools.runtime --config examples/daily_briefing.yaml
uv run python -m tools.runtime --config examples/single_article.yaml --format markdown
```

Run the tools as modules (`python -m tools.<name>`) **from the repository root**. They are not
run by file path (`python tools/runtime.py` no longer works) and the project is not installed, so
from another directory set `PYTHONPATH` to the repository root first.

**WSL / Windows share this folder.** Each needs its own virtualenv: WSL uses the default `.venv` (plain `uv ...`); Windows uses `.venv-win` via `.\scripts\uv-win.ps1 ...` (e.g. `.\scripts\uv-win.ps1 run python -m pytest`).

The CLI prints a JSON runtime result (or markdown with `--format markdown`) and exits
non-zero on `status: failed`.

## Hermes LLM routing

The graph never creates provider clients or reads API keys. The Hermes plugin entry
[`run_langgraph_workflow`](tools/hermes_plugin.py) receives `ctx.llm` and loads
`model_routes` from the plugin's profile-scoped settings.

- A node with an explicit `{provider, model}` route calls `ctx.llm.complete(...)`
  with that route.
- A missing route, or `inherit_cron_model: true`, omits both parameters and
  inherits the model selected for the Cron's main Agent.
- Each completion records its resolved `{node, provider, model}` in the result's
  `llm_audit`. With strict routing enabled, an explicit route mismatch fails.

The plugin configuration, not this skill, must grant narrow override permissions:

```yaml
plugins:
  entries:
    EconomicNewsAnalysis:
      settings:
        model_routes:
          analysis:
            inherit_cron_model: true
      llm:
        allow_provider_override: true
        allow_model_override: true
        allowed_providers: [openrouter]
        allowed_models: [google/gemini-2.5-flash]
```

No `model`, `provider`, provider URL, or API key belongs in `skill.yaml`.

## Hermes native plugin

The repository root contains the native `plugin.yaml` and `__init__.py` entry that
registers one `run_langgraph_workflow` tool. Validate registration without changing
your profile by running:

```bash
hermes plugins doctor --ci .
```

Plugin discovery deliberately defers graph imports, so Doctor verifies the manifest and
registration only. Before an actual Hermes tool call, install this project's runtime
dependencies from `pyproject.toml` into the Python environment used by Hermes; Hermes
does not install them automatically. Then enable the plugin and grant its scoped LLM
override permissions in the Hermes profile.

## Email delivery

Email is sent through [himalaya](https://github.com/pimalaya/himalaya), which is only
configured under **WSL (target Hermes profile)** — native Windows cannot send. Before a real send,
follow the `preflight_checklist` in
[`references/daily_email_agent_workflow.yaml`](references/daily_email_agent_workflow.yaml). With
`email.send: false` the pipeline still renders the `.mml` / preview HTML for inspection.
For the native Hermes tool, scheduled delivery is selected only with:

```json
{"run_mode":"deliver","profile_config":"daily_email"}
```

The selector maps internally to the repository-root
`daily_email_briefing.yaml`; it does not accept configuration paths or email
settings from the model. That tracked file is a safe template with placeholders
and `email.send: false`. A private deployed copy may be completed only in the
controlled Hermes environment before enabling real delivery.

## Testing & smoke

```bash
uv run python -m pytest                               # unit + integration suite
uv run python -m tools.workflow_smoke                 # offline smoke of every workflow
uv run python -m tools.workflow_smoke --only daily-email-briefing --alert-on-fail
```

`workflow_smoke.py` runs each workflow through its example config and writes one JSONL entry
per run to `logs/workflow_smoke.jsonl`; offline mode stubs the LLM and disables
fetch/market/email so it's deterministic and free. It exits `1` if any workflow fails, so a
scheduler or CI step can gate on it. With `--alert-on-fail`, failures are also recorded to
`logs/alerts.jsonl` and forwarded to the command in the `ECON_NEWS_ALERT_CMD` env var (the
one-line summary is passed on stdin and as a trailing argument) — wire it to a himalaya send,
a webhook `curl`, or a desktop notifier. CI (`.github/workflows/ci.yml`) runs pytest + the
offline smoke on every push.

## Key config knobs (`graph` block)

- `temperature` — generation tuning; provider/model routing is plugin-scoped Hermes configuration.
- `research_max_items`, `llm_concurrency` — how many items get LLM analysis, and concurrency.
- `checkpoint_path`, `checkpoint_keep_last` — SQLite checkpoint location and retention (only
  the newest N run threads are kept; older ones are pruned after each run).
- Per-section quotas (`official_max_items` / `media_max_items` / `tech_max_items` /
  `google_max_items` / `hot_max_items` / `movers_max_items`) plus `max_ranked_items`.
- `country_balance` — per-country cap on the official/media boards (default
  `{CN:4, US:4, JP:4, default:99}`) so one country can't crowd out the others; absent disables it.

## Market-mover news (`movers` block)

Market symbols whose 1-day or 1-week move crosses a threshold get a targeted Google News
search, shown in the 📉 异动解读 section. Individual stocks only trigger searches and are hidden
from the trend table; they live in their own file, `sources/stock_watchlist.yaml`
(`market_data.watchlist_config`; set it to `null` to turn the stocks off).

**Editing the stock list after installing the plugin.** Under Hermes the bundled watchlist is
copied once to `<HERMES_HOME>/plugin-data/EconomicNewsAnalysis/stock_watchlist.yaml`, and the
daily email profile reads that copy, so edits there survive reinstalls and upgrades. Each stock
needs `id`, `ticker` (Yahoo Finance), `name` (used in the news query) and `region`; `name_zh` and
`currency` are optional. Delete the copy to restore the default list on the next run. A broken
watchlist is reported as a `WatchlistConfigError` and does not stop the trend table.

The `movers` keys are all optional:

- `enabled` (default `true`), `day_threshold_pct` (4), `week_threshold_pct` (8).
- `max_symbols` (5) and `items_per_symbol` (3) bound the number of searches and headlines.
- `window` (`2d`) is the news lookback; `asset_classes` (`equity_stock`, `equity_index`) selects
  which symbols can trigger.

It only reacts to the current market data, so it cannot backfill a past drop. Skipped in `trend`
mode and when `fetch_enabled` is false.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the graph topology and the
briefing-board / country-balance / macro-background design.
