import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from tools._version import __version__ as PKG_VERSION
from tools.config_loader import ROOT, load_runtime_config
from tools.runtime import execute
from tools.schema_validation import validate_file
from tools.workflow_smoke import _stub_invoke


class _StubLLM:
    """Config-injectable LLM stand-in returning the smoke test's canned JSON.

    Wraps ``workflow_smoke._stub_invoke`` (the single source of truth for the
    offline LLM responses) in the ``.invoke(prompt) -> obj.content`` shape that
    ``ResearchAdapter._invoke`` expects.
    """

    def invoke(self, prompt: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(content=_stub_invoke(self, prompt))


def _hermetic_graph() -> dict:
    """Graph overrides that make an offline run deterministic and self-contained.

    Injecting a stub LLM (and disabling the checkpointer so the live stub is
    never serialized) yields a deterministic ``success`` everywhere.
    """
    return {
        "checkpoint_path": None,
        "_fake_llm": _StubLLM(),
    }


class RuntimeTests(unittest.TestCase):
    def test_analyze_mode_offline(self):
        config = load_runtime_config(ROOT / "examples" / "single_article.yaml")
        result = execute(config)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "analyze")
        self.assertEqual(result["statistics"]["items_analyzed"], 1)
        self.assertEqual(result["skill"]["version"], PKG_VERSION)
        self.assertEqual(result["market_context"]["status"], "skipped")

    def test_cli_outputs_json(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.runtime",
                "--config",
                str(ROOT / "examples" / "single_article.yaml"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "success")

    def test_analyze_markdown_cli(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.runtime",
                "--config",
                str(ROOT / "examples" / "single_article.yaml"),
                "--format",
                "markdown",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=True,
        )
        self.assertIn("## 新闻摘要", completed.stdout)
        self.assertIn("- 地区：美国", completed.stdout)
        self.assertIn("- 主题：货币政策、通胀", completed.stdout)
        self.assertIn("- 汇率：", completed.stdout)

    def test_offline_briefing_and_deliver_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_runtime_config(ROOT / "examples" / "offline_briefing.yaml")
            config["mode"] = "deliver"
            config["output_format"] = "json"
            config["output_dir"] = temp_dir
            config["graph"] = {**config.get("graph", {}), **_hermetic_graph()}
            config["deliver_email"] = True
            config["email"] = {
                "from": "from@example.com",
                "to": "to@example.com",
                "subject": "宏观简报",
                "send": False,
            }
            result = execute(config)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["status"], result["pipeline_status"])
            self.assertIn("markdown", result["output_files"])
            self.assertIn("json", result["output_files"])
            self.assertNotIn("mml", result["output_files"])
            self.assertEqual(result["delivery_status"], "skipped")
            self.assertEqual(result["delivery_kind"], "none")
            saved = json.loads(Path(result["output_files"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual(saved, result)

    def test_remaining_modes_dispatch_offline(self):
        base = load_runtime_config(ROOT / "examples" / "offline_briefing.yaml")
        base["fetch_enabled"] = False
        base["graph"] = {**base.get("graph", {}), **_hermetic_graph()}
        for mode in ["fetch", "briefing"]:
            with self.subTest(mode=mode):
                config = dict(base)
                config["mode"] = mode
                config["output_format"] = "json"
                result = execute(config)
                self.assertEqual(result["status"], "success")
                self.assertEqual(result["mode"], mode)

        list_config = dict(base)
        list_config["mode"] = "list-sources"
        listed = execute(list_config)
        self.assertGreater(len(listed["sources"]), 0)

        check_config = dict(base)
        check_config["mode"] = "check"
        checked = execute(check_config)
        self.assertIn(checked["check"]["action"], {"fetch", "skip"})

    def test_cli_failure_uses_runtime_result_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_config = Path(temp_dir) / "bad.json"
            # Unknown top-level keys are tolerated by design (schema
            # additionalProperties: true), so use a genuine schema violation —
            # graph.temperature is constrained to [0, 2].
            bad_config.write_text('{"graph": {"temperature": 9}}', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.runtime",
                    "--config",
                    str(bad_config),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(completed.returncode, 1)
            result = json.loads(completed.stderr)
            self.assertEqual(result["status"], "failed")
            validate_file(
                result,
                ROOT / "schemas" / "runtime_result.schema.json",
            )


if __name__ == "__main__":
    unittest.main()
