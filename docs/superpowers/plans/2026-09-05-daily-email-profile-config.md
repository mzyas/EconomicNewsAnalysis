# Daily Email Profile Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fixed, safe `daily_email` configuration selector to the Hermes native tool for scheduled daily delivery.

**Architecture:** The native plugin schema exposes a single profile enum instead of file paths. `tools.hermes_plugin` maps that enum to the repository-root template, resolves it under the project root, then loads it through the existing runtime configuration loader. The static template stays safe to commit by containing placeholders and disabling actual mail send.

**Tech Stack:** Python 3.10+, Hermes native plugin interface, PyYAML/JSON configuration, unittest, pytest.

**Spec:** `docs/daily-email-profile-config-design.md`

## Global Constraints

- Accept no configuration path, mail recipient, sender, or mail-send flag from native tool arguments.
- The only profile selector is `profile_config: "daily_email"`.
- `run_mode: "deliver"` requires that selector; the selector permits empty `source_text` for scheduled fetches.
- `daily_email_briefing.yaml` is tracked at repository root with no personal information and `email.send: false`.
- Resolve the mapped file and reject it unless it is a regular file under the resolved project root.
- Preserve existing non-delivery tool calls and do not modify unrelated local changes.

---

### Task 1: Define the native tool boundary

**Files:**
- Modify: `tests/test_hermes_native_plugin.py`
- Modify: `__init__.py`

**Interfaces:**
- Consumes: Hermes tool arguments as `dict[str, Any]`.
- Produces: `run_langgraph_workflow(ctx, source_text, run_mode=..., profile_config=...)`.

- [ ] **Step 1: Write the failing test**

```python
self.assertNotIn("source_text", schema["required"])
self.assertEqual(schema["properties"]["profile_config"]["enum"], ["daily_email"])
handler({"run_mode": "deliver"})  # raises ValueError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hermes_native_plugin.py -q`

Expected: FAIL because the schema has no selector and delivery accepts no selector.

- [ ] **Step 3: Write minimal implementation**

```python
profile_config = args.get("profile_config")
if run_mode == "deliver" and profile_config != "daily_email":
    raise ValueError("run_mode 'deliver' requires profile_config 'daily_email'")
```

Add the closed `profile_config` schema enum, allow empty text only for that
selector, and forward it to the wrapper.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hermes_native_plugin.py -q`

Expected: PASS.

### Task 2: Load the fixed, contained profile configuration

**Files:**
- Modify: `tests/test_hermes_llm_routing.py`
- Modify: `tools/hermes_plugin.py`

**Interfaces:**
- Consumes: `profile_config: str | None` and internal fixed mappings.
- Produces: runtime config loaded from the contained `daily_email_briefing.yaml` or a clear `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
with patch.object(plugin, "ROOT", temp_root):
    self.assertEqual(plugin.resolve_profile_config("daily_email"), template)
with patch.object(plugin, "_PROFILE_CONFIG_FILENAMES", {"daily_email": "../outside.yaml"}):
    with self.assertRaisesRegex(ValueError, "must remain inside"):
        plugin.resolve_profile_config("daily_email")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_hermes_llm_routing.py -q`

Expected: FAIL because no resolver exists.

- [ ] **Step 3: Write minimal implementation**

```python
candidate = (ROOT / filename).resolve(strict=True)
candidate.relative_to(ROOT.resolve(strict=True))
if not candidate.is_file():
    raise ValueError("profile configuration must be a regular file")
```

Load the result using `load_runtime_config(candidate)` only when no explicit
test config was supplied, preserving explicit test configuration injection.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_hermes_llm_routing.py -q`

Expected: PASS.

### Task 3: Publish the safe template and operator guidance

**Files:**
- Create: `daily_email_briefing.yaml`
- Delete: `examples/daily_email_briefing.yaml`
- Modify: `workflows.yaml`
- Modify: `README.md`
- Modify: `docs/HERMES-KARL-FIRST-INSTALL.md`
- Modify: `tests/test_config_loader.py`

**Interfaces:**
- Consumes: existing runtime schema and `load_runtime_config`.
- Produces: one repository-root safe daily-delivery template used by workflow documentation and the native selector.

- [ ] **Step 1: Write the failing test**

```python
config = load_runtime_config(ROOT / "daily_email_briefing.yaml")
self.assertTrue(config["deliver_email"])
self.assertFalse(config["email"]["send"])
self.assertNotIn("@", config["email"]["to"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_config_loader.py -q`

Expected: FAIL because the root template does not exist.

- [ ] **Step 3: Write minimal implementation**

Create the root template with project-local sources/output references,
placeholder mail fields, and `email.send: false`. Point the delivery workflow
and documentation to it; remove the former tracked example containing a
personal address.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_config_loader.py -q`

Expected: PASS.

### Task 4: Verify and commit the approved change

**Files:**
- Verify: `__init__.py`, `tools/hermes_plugin.py`, `daily_email_briefing.yaml`, docs, and focused tests.

- [ ] **Step 1: Run focused regression tests**

Run: `uv run python -m pytest tests/test_hermes_native_plugin.py tests/test_hermes_native_plugin_execution.py tests/test_hermes_llm_routing.py tests/test_config_loader.py -q`

Expected: PASS with no test failures.

- [ ] **Step 2: Run native registration validation**

Run: `hermes plugins doctor --ci .`

Expected: one registered `run_langgraph_workflow` tool and exit code 0.

- [ ] **Step 3: Check the diff and stage only task files**

Run: `git -c core.whitespace=cr-at-eol diff --check` and `git status --short`

Expected: no whitespace errors; the pre-existing credential and alerting edits remain unstaged.

- [ ] **Step 4: Commit**

```bash
git add __init__.py tools/hermes_plugin.py daily_email_briefing.yaml \
  examples/daily_email_briefing.yaml workflows.yaml README.md \
  docs/daily-email-profile-config-design.md \
  docs/superpowers/plans/2026-09-05-daily-email-profile-config.md \
  docs/HERMES-KARL-FIRST-INSTALL.md tests/test_hermes_native_plugin.py \
  tests/test_hermes_llm_routing.py tests/test_config_loader.py
git commit -m "feat: add safe daily email profile selector"
```
