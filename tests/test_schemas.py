import json
import unittest
from pathlib import Path

from tools.config_loader import ROOT, load_runtime_config
from tools.runtime import execute
from tools.schema_validation import validate_file


class SchemaTests(unittest.TestCase):
    def test_all_schema_files_parse(self):
        for path in (ROOT / "schemas").glob("*.json"):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_runtime_schema_declares_decoupled_status_fields(self):
        schema = json.loads(
            (ROOT / "schemas" / "runtime_result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["properties"]["pipeline_status"]["enum"],
            ["running", "success", "partial_success", "failed"],
        )
        self.assertEqual(
            schema["properties"]["delivery_kind"]["enum"],
            [
                "report_with_attachment",
                "report_without_attachment",
                "failure_notification",
                "none",
            ],
        )
        for field in (
            "pipeline_status", "output_status", "delivery_status", "delivery_kind"
        ):
            self.assertIn(field, schema["required"])

    def test_runtime_output_validates(self):
        config = load_runtime_config(ROOT / "examples" / "single_article.yaml")
        result = execute(config)
        validate_file(
            result,
            ROOT / "schemas" / "runtime_result.schema.json",
        )


if __name__ == "__main__":
    unittest.main()
