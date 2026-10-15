# Daily email profile configuration design

## Goal

Allow Hermes Cron to run the full daily delivery workflow without accepting a
model-supplied configuration path or recipient settings.

## Boundary

The native `run_langgraph_workflow` tool exposes only the closed
`profile_config` enum. Its sole supported value, `daily_email`, maps to the
repository-root `daily_email_briefing.yaml`. The handler does not accept
configuration paths, email addresses, or mail-send switches from tool
arguments.

`run_mode: "deliver"` requires `profile_config: "daily_email"`. Other modes
retain the existing manual-text behavior and do not load the daily profile.
With `daily_email`, `source_text` may be omitted because the workflow fetches
its configured RSS and market inputs itself.

## Safe template

`daily_email_briefing.yaml` is a tracked, no-personal-information template.
It contains the delivery workflow, RSS and market-data references, and a
project-local output directory. Its `email.from` and `email.to` fields are
plain placeholders and `email.send` is `false`, so a source checkout cannot
send mail until a private deployment copy is deliberately completed.

The preceding example file is removed so the repository has one canonical
daily-delivery template and no tracked personal address.

## Path containment

Before loading the mapped file, the plugin resolves both the project root and
the candidate file. The candidate must exist as a regular file and remain
inside the resolved project root. This rejects a replaced symlink or any
future mapping error that escapes the repository.
