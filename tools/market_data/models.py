"""Market data models shared by providers and Runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MarketSymbol:
    id: str
    ticker: str
    name: str
    asset_class: str
    region: str
    currency: str | None = None
    asset_type: str = "price"
    display_precision: int = 2
    name_zh: str | None = None


@dataclass
class MarketSnapshot:
    symbol_id: str
    ticker: str
    name: str
    asset_class: str
    region: str
    last_price: float | None
    change_pct: float | None
    period: str
    interval: str
    as_of: str | None
    provider: str
    status: str
    error: str | None = None
    asset_type: str = "price"
    display_precision: int = 2
    name_zh: str | None = None
    change_1w_pct: float | None = None
    change_1m_pct: float | None = None
    change_1w_abs: float | None = None
    change_1m_abs: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketDataResult:
    enabled: bool
    provider: str | None
    status: str
    snapshots: list[MarketSnapshot] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "status": self.status,
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "errors": self.errors,
        }
