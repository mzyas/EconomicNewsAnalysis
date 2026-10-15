"""yfinance-backed market data provider."""

from __future__ import annotations

from typing import Any

from ..models import MarketSnapshot, MarketSymbol


class YFinanceProvider:
    name = "yfinance"

    def fetch_snapshots(
        self,
        symbols: list[MarketSymbol],
        period: str,
        interval: str,
    ) -> list[MarketSnapshot]:
        try:
            import yfinance as yf
        except ImportError as exc:
            detail = f"yfinance import failed: {exc}"
            return [
                self._failed(symbol, period, interval, detail)
                for symbol in symbols
            ]

        snapshots: list[MarketSnapshot] = []
        for symbol in symbols:
            try:
                # Fetch enough history for multi-period (1w/1m) calculations.
                # Use at least "3mo" so we have ~66 daily bars for 21-day offset.
                min_period = _minimum_period(period, "3mo")
                history = yf.Ticker(symbol.ticker).history(
                    period=min_period,
                    interval=interval,
                )
                snapshots.append(
                    self._snapshot_from_history(symbol, history, period, interval)
                )
            except Exception as exc:
                snapshots.append(self._failed(symbol, period, interval, str(exc)))
        return snapshots

    def _snapshot_from_history(
        self,
        symbol: MarketSymbol,
        history: Any,
        period: str,
        interval: str,
    ) -> MarketSnapshot:
        if history is None or getattr(history, "empty", False):
            return self._failed(symbol, period, interval, "no market data returned")

        close = history["Close"].dropna()
        if len(close) == 0:
            return self._failed(symbol, period, interval, "no close prices returned")

        close = self._supplement_latest_session(close, symbol.ticker)

        last_price = float(close.iloc[-1])

        # ── 1-day change (backward-compatible `change_pct`) ─────────
        change_pct = None
        if len(close) >= 2:
            previous = float(close.iloc[-2])
            if previous != 0:
                change_pct = ((last_price - previous) / previous) * 100

        # ── 1-week change (5 trading-day offset) ────────────────────
        change_1w_pct = None
        change_1w_abs = None
        if len(close) >= 6:
            prev_1w = float(close.iloc[-6])
            if prev_1w != 0:
                change_1w_pct = ((last_price - prev_1w) / prev_1w) * 100
            change_1w_abs = last_price - prev_1w

        # ── 1-month change (21 trading-day offset) ──────────────────
        change_1m_pct = None
        change_1m_abs = None
        if len(close) >= 22:
            prev_1m = float(close.iloc[-22])
            if prev_1m != 0:
                change_1m_pct = ((last_price - prev_1m) / prev_1m) * 100
            change_1m_abs = last_price - prev_1m

        as_of = None
        if hasattr(close, "index") and len(close.index) > 0:
            as_of = str(close.index[-1])

        return MarketSnapshot(
            symbol_id=symbol.id,
            ticker=symbol.ticker,
            name=symbol.name,
            name_zh=symbol.name_zh,
            asset_class=symbol.asset_class,
            region=symbol.region,
            last_price=last_price,
            change_pct=change_pct,
            period=period,
            interval=interval,
            as_of=as_of,
            provider=self.name,
            status="success",
            asset_type=symbol.asset_type,
            display_precision=symbol.display_precision,
            change_1w_pct=change_1w_pct,
            change_1m_pct=change_1m_pct,
            change_1w_abs=change_1w_abs,
            change_1m_abs=change_1m_abs,
        )

    def _supplement_latest_session(self, close: Any, ticker: str) -> Any:
        """Splice in the newest session close from intraday data when the daily
        series lags.

        Yahoo's *daily* candle for many non-US indices (Nikkei, Hang Seng, FTSE,
        DAX) trails the actual latest session by ~1 day while the intraday feed
        already carries it — so a Saturday briefing showed Thursday's Nikkei
        instead of Friday's (a ~4% gap on a volatile day). We keep the daily
        series as the source of truth for the historical 1w/1m offsets and only
        append the newest session's close from intraday when it is strictly
        newer than the daily series' last bar. Best-effort: a non-datetime
        index, missing intraday data or any network error leaves the daily
        series untouched (and, e.g., keeps unit tests with fake indices inert).
        """
        try:
            import pandas as pd
            import yfinance as yf

            daily_date = close.index[-1].normalize()
            # Skip the extra fetch when the daily bar already shows today's
            # session (the common case for current/US tickers) — only laggards
            # pay for the intraday round-trip.
            now = pd.Timestamp.now(tz=daily_date.tz).normalize()
            if (now - daily_date).days < 1:
                return close

            intraday = yf.Ticker(ticker).history(period="5d", interval="60m")
            if intraday is None or getattr(intraday, "empty", False):
                return close
            iclose = intraday["Close"].dropna()
            if len(iclose) == 0:
                return close

            latest_session = iclose.index[-1].normalize()
            if latest_session <= daily_date:
                return close

            extended = close.copy()
            extended.loc[latest_session] = float(iclose.iloc[-1])
            return extended.sort_index()
        except Exception:
            return close

    def _failed(
        self,
        symbol: MarketSymbol,
        period: str,
        interval: str,
        error: str,
    ) -> MarketSnapshot:
        return MarketSnapshot(
            symbol_id=symbol.id,
            ticker=symbol.ticker,
            name=symbol.name,
            name_zh=symbol.name_zh,
            asset_class=symbol.asset_class,
            region=symbol.region,
            last_price=None,
            change_pct=None,
            period=period,
            interval=interval,
            as_of=None,
            provider=self.name,
            status="failed",
            error=error,
            asset_type=symbol.asset_type,
            display_precision=symbol.display_precision,
        )


def _minimum_period(requested: str, minimum: str = "3mo") -> str:
    """Return whichever period string is longer.

    yfinance period rank (shorter → longer):
        1d, 2d, 5d, 1wk, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max
    """
    _RANK = {
        "1d": 1, "2d": 2, "5d": 3, "1wk": 4, "1mo": 5,
        "3mo": 6, "6mo": 7, "1y": 8, "2y": 9, "5y": 10, "10y": 11,
        "max": 12,
    }
    req_rank = _RANK.get(requested, 0)
    min_rank = _RANK.get(minimum, 6)
    return requested if req_rank >= min_rank else minimum
