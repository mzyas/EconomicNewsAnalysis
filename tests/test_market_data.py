import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.config_loader import ROOT, load_runtime_config
from tools.market_data.loader import (
    build_market_context,
    load_market_sources,
    load_stock_watchlist,
)
from tools.market_data.models import MarketSnapshot
from tools.market_data.providers.yfinance_provider import YFinanceProvider
from tools.market_data.registry import (
    get_market_data_provider,
    register_market_data_provider,
)
from tools.runtime import execute
from tools.schema_validation import validate_file


class FakeProvider:
    name = "fake"

    def fetch_snapshots(self, symbols, period, interval):
        return [
            MarketSnapshot(
                symbol_id=symbols[0].id,
                ticker=symbols[0].ticker,
                name=symbols[0].name,
                asset_class=symbols[0].asset_class,
                region=symbols[0].region,
                last_price=100.0,
                change_pct=1.0,
                period=period,
                interval=interval,
                as_of="2026-06-17",
                provider=self.name,
                status="success",
            )
        ]


class MarketDataTests(unittest.TestCase):
    def test_market_sources_schema_validates(self):
        data = load_market_sources(ROOT / "sources" / "market_sources.yaml")
        validate_file(data, ROOT / "schemas" / "market_sources.schema.json")
        self.assertEqual(data["provider"], "yfinance")
        self.assertGreaterEqual(len(data["symbols"]), 4)

    def test_stocks_live_only_in_the_watchlist(self):
        core = load_market_sources(ROOT / "sources" / "market_sources.yaml")["symbols"]
        stocks = load_stock_watchlist(ROOT / "sources" / "stock_watchlist.yaml")
        self.assertNotIn("equity_stock", {item["asset_class"] for item in core})
        self.assertEqual(len(stocks), 25)
        self.assertEqual(
            {(s["asset_class"], s["asset_type"], s["display_precision"]) for s in stocks},
            {("equity_stock", "price", 2)},
        )
        self.assertFalse({s["id"] for s in stocks} & {s["id"] for s in core})

    def _market_context(self, watchlist_config):
        recorded = []

        class RecordingProvider:
            name = "recording"

            def fetch_snapshots(self, symbols, period, interval):
                recorded.extend(symbols)
                return [
                    MarketSnapshot(
                        symbol_id=s.id,
                        ticker=s.ticker,
                        name=s.name,
                        asset_class=s.asset_class,
                        region=s.region,
                        last_price=1.0,
                        change_pct=0.0,
                        period=period,
                        interval=interval,
                        as_of=None,
                        provider=self.name,
                        status="success",
                    )
                    for s in symbols
                ]

        register_market_data_provider("recording", RecordingProvider)
        config = {
            "market_data": {
                "enabled": True,
                "provider": "recording",
                "source_config": "sources/market_sources.yaml",
                "watchlist_config": watchlist_config,
            }
        }
        return build_market_context(config), recorded

    def test_watchlist_symbols_are_appended_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            watchlist = Path(temp_dir) / "stock_watchlist.yaml"
            watchlist.write_text(
                "symbols:\n"
                "  - id: sp500\n    ticker: DUP\n    name: Duplicate\n    region: US\n"
                "  - id: sony\n    ticker: 6758.T\n    name: Sony Group\n    region: JP\n",
                encoding="utf-8",
            )
            context, symbols = self._market_context(str(watchlist))
        self.assertEqual(context["status"], "success")
        by_id = {s.id: s for s in symbols}
        self.assertEqual(by_id["sp500"].ticker, "^GSPC")
        self.assertEqual(by_id["sony"].asset_class, "equity_stock")
        self.assertEqual(symbols[-1].id, "sony")

    def test_broken_or_disabled_watchlist_keeps_core_symbols(self):
        core = len(load_market_sources(ROOT / "sources" / "market_sources.yaml")["symbols"])
        with tempfile.TemporaryDirectory() as temp_dir:
            broken = Path(temp_dir) / "stock_watchlist.yaml"
            broken.write_text("symbols:\n  - id: x\n    ticker: X\n", encoding="utf-8")
            for watchlist in (str(broken), str(Path(temp_dir) / "missing.yaml")):
                with self.subTest(watchlist=watchlist):
                    context, symbols = self._market_context(watchlist)
                    self.assertEqual(context["status"], "success")
                    self.assertEqual(len(symbols), core)
                    self.assertEqual(context["errors"][0]["type"], "WatchlistConfigError")
        context, symbols = self._market_context(None)
        self.assertEqual((len(symbols), context["errors"]), (core, []))

    def test_runtime_config_defaults_to_bundled_watchlist(self):
        config = load_runtime_config(ROOT / "examples" / "daily_trend.yaml")
        self.assertEqual(
            Path(config["market_data"]["watchlist_config"]),
            ROOT / "sources" / "stock_watchlist.yaml",
        )

    def test_registry_returns_registered_provider(self):
        register_market_data_provider("fake", FakeProvider)
        provider = get_market_data_provider("fake")
        self.assertEqual(provider.name, "fake")

    def test_market_context_skipped_by_default(self):
        config = load_runtime_config(ROOT / "examples" / "single_article.yaml")
        result = execute(config)
        self.assertEqual(result["market_context"]["status"], "skipped")
        self.assertFalse(result["market_context"]["enabled"])

    def test_market_context_uses_registered_provider(self):
        register_market_data_provider("fake", FakeProvider)
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "market.json"
            config_path.write_text(
                """{
                  "mode": "analyze",
                  "market_data": {
                    "enabled": true,
                    "provider": "fake",
                    "source_config": "sources/market_sources.yaml",
                    "period": "5d",
                    "interval": "1d"
                  },
                  "input_items": [{"title": "Test", "summary": "Test", "source": "test"}]
                }""",
                encoding="utf-8",
            )
            config = load_runtime_config(config_path)
            result = execute(config)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["market_context"]["status"], "success")
        self.assertEqual(result["market_context"]["snapshots"][0]["provider"], "fake")

    def test_trend_mode_runs_offline_with_fake_provider(self):
        # daily-trend needs market data; the fake provider lets it run through
        # offline (no network, no LLM) to a deterministic success — the contract
        # the offline workflow smoke relies on.
        from tools.market_data.providers.fake_provider import FakeMarketProvider

        register_market_data_provider("fake", FakeMarketProvider)
        config = load_runtime_config(ROOT / "examples" / "daily_trend.yaml")
        config["market_data"] = {**config["market_data"], "provider": "fake"}
        result = execute(config)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["trend_table"]["status"], "success")
        self.assertTrue(result["trend_table"]["rows"])

    def test_unknown_provider_does_not_fail_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "market.json"
            config_path.write_text(
                """{
                  "mode": "analyze",
                  "market_data": {
                    "enabled": true,
                    "provider": "unknown",
                    "source_config": "sources/market_sources.yaml"
                  },
                  "input_items": [{"title": "Test", "summary": "Test", "source": "test"}]
                }""",
                encoding="utf-8",
            )
            config = load_runtime_config(config_path)
            result = execute(config)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["market_context"]["status"], "failed")
        self.assertIn(
            "Unknown market data provider", result["market_context"]["errors"][0]["message"]
        )

    def test_yfinance_provider_uses_fake_module(self):
        class FakeIloc:
            def __init__(self, values):
                self.values = values

            def __getitem__(self, index):
                return self.values[index]

        class FakeClose:
            def __init__(self):
                self.values = [100.0, 105.0]
                self.index = ["2026-06-16", "2026-06-17"]
                self.iloc = FakeIloc(self.values)

            def dropna(self):
                return self

            def __len__(self):
                return len(self.values)

        class FakeHistory:
            empty = False

            def __getitem__(self, key):
                if key != "Close":
                    raise KeyError(key)
                return FakeClose()

        class FakeTicker:
            def __init__(self, ticker):
                self.ticker = ticker

            def history(self, period, interval):
                return FakeHistory()

        fake_yfinance = types.SimpleNamespace(Ticker=FakeTicker)
        symbol = load_market_sources(ROOT / "sources" / "market_sources.yaml")["symbols"][0]
        with patch.dict(sys.modules, {"yfinance": fake_yfinance}):
            snapshots = YFinanceProvider().fetch_snapshots(
                symbols=[type("Symbol", (), symbol)()],
                period="5d",
                interval="1d",
            )
        self.assertEqual(snapshots[0].status, "success")
        self.assertEqual(snapshots[0].last_price, 105.0)
        self.assertEqual(round(snapshots[0].change_pct, 2), 5.0)


if __name__ == "__main__":
    unittest.main()
