"""How the deliberately broad exception handlers report failures.

These handlers keep catching every exception on purpose (one failing source,
item or call must not stop the run). The tests pin that the visible result is
unchanged and that each of them now leaves a traceback in the log instead of
swallowing the error.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.email_delivery import send_email
from tools.evidence_fetcher import fetch_evidence
from tools.market_data.loader import build_market_context
from tools.market_data.models import MarketSymbol
from tools.market_data.providers.yfinance_provider import YFinanceProvider
from tools.market_data.registry import register_market_data_provider


def has_traceback(records) -> bool:
    return any(record.exc_info for record in records)


class SendEmailFailureTests(unittest.TestCase):
    CONFIG = {"from": "a@b.com", "to": "c@d.com"}

    def test_send_failure_is_returned_and_logged_with_traceback(self):
        with (
            patch("tools.email_delivery.send_mml", side_effect=RuntimeError("smtp down")),
            self.assertLogs("tools.email_delivery", level="ERROR") as logs,
        ):
            result = send_email(email_config=self.CONFIG, subject="s", text_body="t")
        self.assertFalse(result.sent)
        self.assertEqual(result.error, "smtp down")
        self.assertTrue(has_traceback(logs.records))


class MarketDataFailureTests(unittest.TestCase):
    def test_provider_failure_gives_failed_result_and_logs(self):
        class ExplodingProvider:
            name = "exploding"

            def fetch_snapshots(self, symbols, period, interval):
                raise ConnectionError("provider offline")

        register_market_data_provider("exploding", ExplodingProvider)
        config = {
            "market_data": {
                "enabled": True,
                "provider": "exploding",
                "source_config": "sources/market_sources.yaml",
            }
        }
        with self.assertLogs("tools.market_data.loader", level="ERROR") as logs:
            context = build_market_context(config)
        self.assertEqual(context["status"], "failed")
        self.assertEqual(
            context["errors"], [{"type": "ConnectionError", "message": "provider offline"}]
        )
        self.assertTrue(has_traceback(logs.records))

    def test_broken_watchlist_is_reported_and_logged_but_core_symbols_stay(self):
        seen = []

        class RecordingProvider:
            name = "recording-watchlist"

            def fetch_snapshots(self, symbols, period, interval):
                seen.extend(symbols)
                return []

        register_market_data_provider("recording-watchlist", RecordingProvider)
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = str(Path(temp_dir) / "missing.yaml")
            config = {
                "market_data": {
                    "enabled": True,
                    "provider": "recording-watchlist",
                    "source_config": "sources/market_sources.yaml",
                    "watchlist_config": missing,
                }
            }
            with self.assertLogs("tools.market_data.loader", level="WARNING") as logs:
                context = build_market_context(config)
        self.assertEqual(context["errors"][0]["type"], "WatchlistConfigError")
        self.assertTrue(seen)  # the core symbols were still requested
        self.assertTrue(has_traceback(logs.records))


class YFinanceFailureTests(unittest.TestCase):
    SYMBOL = MarketSymbol(id="x", ticker="X", name="X", asset_class="equity", region="US")

    def test_failing_symbol_becomes_a_failed_snapshot_and_is_logged(self):
        with (
            patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")),
            self.assertLogs(
                "tools.market_data.providers.yfinance_provider", level="WARNING"
            ) as logs,
        ):
            snapshots = YFinanceProvider().fetch_snapshots([self.SYMBOL], "5d", "1d")
        self.assertEqual(
            [(s.symbol_id, s.status, s.error) for s in snapshots], [("x", "failed", "rate limited")]
        )
        self.assertTrue(has_traceback(logs.records))

    def test_intraday_splice_failure_keeps_the_daily_series_and_logs_at_debug(self):
        close = object()  # no ``.index``: anything unexpected must leave the series untouched
        with self.assertLogs(
            "tools.market_data.providers.yfinance_provider", level="DEBUG"
        ) as logs:
            result = YFinanceProvider()._supplement_latest_session(close, "X")
        self.assertIs(result, close)
        self.assertTrue(has_traceback(logs.records))


class EvidenceCloseFailureTests(unittest.TestCase):
    @patch("tools.evidence_fetcher.requests.get")
    def test_close_failure_does_not_change_the_result_but_is_logged(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.iter_content.return_value = [
            b"<html><body><article>Rates were left unchanged.</article></body></html>"
        ]
        response.apparent_encoding = "utf-8"
        response.close.side_effect = OSError("socket already closed")
        mock_get.return_value = response
        with self.assertLogs("tools.evidence_fetcher", level="DEBUG") as logs:
            result = fetch_evidence("https://example.com/news")
        self.assertIsNone(result["error"])
        self.assertIn("Rates were left unchanged", result["content"])
        self.assertTrue(has_traceback(logs.records))


if __name__ == "__main__":
    unittest.main()
