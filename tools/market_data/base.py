"""Market data provider interface."""

from __future__ import annotations

from typing import Protocol

from .models import MarketSnapshot, MarketSymbol


class MarketDataProvider(Protocol):
    name: str

    def fetch_snapshots(
        self,
        symbols: list[MarketSymbol],
        period: str,
        interval: str,
    ) -> list[MarketSnapshot]:
        ...
