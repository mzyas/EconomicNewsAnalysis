"""Deterministic in-memory market data provider for offline runs and tests.

The market-data analogue of ``workflow_smoke._stub_invoke`` for the LLM: it
returns canned, successful snapshots so the trend path can run end-to-end with
no network. It is intentionally NOT in the default registry — production stays
yfinance-only. Callers opt in explicitly via
``register_market_data_provider("fake", FakeMarketProvider)``.
"""

from __future__ import annotations

from tools.market_data.models import MarketSnapshot, MarketSymbol


class FakeMarketProvider:
    name = "fake"

    def fetch_snapshots(
        self,
        symbols: list[MarketSymbol],
        period: str,
        interval: str,
    ) -> list[MarketSnapshot]:
        """One successful, deterministic snapshot per symbol (canned numbers)."""
        return [
            MarketSnapshot(
                symbol_id=symbol.id,
                ticker=symbol.ticker,
                name=symbol.name,
                asset_class=symbol.asset_class,
                region=symbol.region,
                last_price=100.0,
                change_pct=1.0,
                period=period,
                interval=interval,
                as_of="2026-06-17",
                provider=self.name,
                status="success",
                asset_type=symbol.asset_type,
                display_precision=symbol.display_precision,
                name_zh=symbol.name_zh,
                change_1w_pct=2.0,
                change_1m_pct=3.0,
                change_1w_abs=2.0,
                change_1m_abs=3.0,
            )
            for symbol in symbols
        ]
