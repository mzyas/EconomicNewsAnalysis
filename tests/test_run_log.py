import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.run_log import append_log, evaluate_fetch_window


class RunLogTests(unittest.TestCase):
    def test_same_day_skips_fetch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.jsonl"
            now = datetime(2026, 6, 10, 3, 0, tzinfo=timezone.utc)
            append_log(path, {"fetched_at": "2026-06-10T01:00:00+00:00"})
            result = evaluate_fetch_window(path, now)
            self.assertEqual(result["action"], "skip")


if __name__ == "__main__":
    unittest.main()

