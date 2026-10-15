import re
import unittest

from tools.config_loader import ROOT, _load_mapping, load_runtime_config
from tools.runtime import MODES
from tools.schema_validation import validate_file


class WorkflowRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = _load_mapping(ROOT / "workflows.yaml")
        cls.workflows = cls.registry["workflows"]

    def test_registry_validates_against_schema(self):
        validate_file(
            self.registry,
            ROOT / "schemas" / "workflows.schema.json",
        )

    def test_workflow_modes_and_example_configs_match(self):
        for workflow_id, workflow in self.workflows.items():
            with self.subTest(workflow=workflow_id):
                self.assertIn(workflow["mode"], MODES)
                description = workflow["description"]
                self.assertRegex(description, re.compile(r"[\u4e00-\u9fff]"))

                example_path = ROOT / workflow["example_config"]
                self.assertTrue(example_path.is_file())
                config = load_runtime_config(example_path)
                self.assertEqual(config["mode"], workflow["mode"])

    def test_manual_analysis_and_briefing_modes_are_explicit(self):
        self.assertEqual(
            self.workflows["news-analysis"]["activation"],
            "manual",
        )
        self.assertEqual(
            self.workflows["daily-briefing"]["mode"],
            "briefing",
        )
        self.assertEqual(
            self.workflows["daily-email-briefing"]["mode"],
            "deliver",
        )


if __name__ == "__main__":
    unittest.main()
