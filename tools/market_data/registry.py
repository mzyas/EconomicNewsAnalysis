"""Market data provider registry."""

from __future__ import annotations

from .base import MarketDataProvider
from .providers.yfinance_provider import YFinanceProvider


_PROVIDERS: dict[str, type[MarketDataProvider]] = {
    YFinanceProvider.name: YFinanceProvider,
}


def register_market_data_provider(
    name: str,
    provider_cls: type[MarketDataProvider],
) -> None:
    _PROVIDERS[name] = provider_cls


def get_market_data_provider(name: str) -> MarketDataProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown market data provider: {name}") from exc
