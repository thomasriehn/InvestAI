"""Market data layer.

Fetches historical and current quotes via yfinance, falls back to a synthetic
price generator when the network is unavailable so the rest of the system
remains testable in isolated environments.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import pandas as pd

from ..db.store import Store
from ..utils.logging import get_logger

log = get_logger(__name__)

try:
    import yfinance as yf  # noqa: F401
    _HAS_YF = True
except Exception:  # pragma: no cover - optional dep at runtime
    _HAS_YF = False


@dataclass
class Quote:
    ticker: str
    price: float
    currency: str
    asof: datetime


def _row(date: pd.Timestamp, row: pd.Series) -> dict:
    def f(key, alt=None):
        v = row.get(key, alt)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return float(v)
    return {
        "date": pd.Timestamp(date).date().isoformat(),
        "open": f("Open"), "high": f("High"), "low": f("Low"),
        "close": f("Close"),
        "adj_close": f("Adj Close", row.get("Close")),
        "volume": f("Volume"),
    }


def _synthetic_history(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Deterministic geometric-Brownian-motion fallback. Seeded by ticker."""
    rng = random.Random(hash(ticker) & 0xFFFFFFFF)
    days = pd.bdate_range(start.date(), end.date())
    if len(days) == 0:
        return pd.DataFrame()
    mu = 0.07 / 252
    sigma = 0.20 / math.sqrt(252)
    price = 50 + (hash(ticker) % 200)
    closes = []
    for _ in days:
        shock = rng.gauss(0, 1)
        price = max(0.5, price * math.exp(mu - 0.5 * sigma * sigma + sigma * shock))
        closes.append(price)
    closes = np.array(closes)
    df = pd.DataFrame({
        "Open": closes * (1 + np.array([rng.gauss(0, 0.002) for _ in closes])),
        "High": closes * (1 + np.abs([rng.gauss(0, 0.004) for _ in closes])),
        "Low":  closes * (1 - np.abs([rng.gauss(0, 0.004) for _ in closes])),
        "Close": closes,
        "Adj Close": closes,
        "Volume": [rng.randint(1_000, 1_000_000) for _ in closes],
    }, index=days)
    return df


class MarketData:
    """Wraps yfinance with caching to the local SQLite store."""

    def __init__(self, store: Store, *, allow_synthetic: bool = True) -> None:
        self.store = store
        self.allow_synthetic = allow_synthetic

    # ---- history ----------------------------------------------------------
    def history(self, ticker: str, lookback_days: int = 504,
                refresh: bool = True) -> pd.DataFrame:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(lookback_days * 1.6) + 10)
        if refresh:
            self._refresh_history(ticker, start, end)
        rows = self.store.fetch_prices(ticker)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df

    def _refresh_history(self, ticker: str, start: datetime, end: datetime) -> None:
        df: pd.DataFrame | None = None
        if _HAS_YF:
            try:
                t = yf.Ticker(ticker)
                df = t.history(start=start.date().isoformat(),
                               end=end.date().isoformat(),
                               auto_adjust=False)
            except Exception as e:  # network / parsing failure
                log.warning("yfinance history failed for %s: %s", ticker, e)
                df = None
        if df is None or df.empty:
            if not self.allow_synthetic:
                return
            log.info("Using synthetic data for %s (yfinance unavailable)", ticker)
            df = _synthetic_history(ticker, start, end)
        rows = [_row(idx, r) for idx, r in df.iterrows()]
        self.store.upsert_prices(ticker, rows)

    # ---- quote ------------------------------------------------------------
    def quote(self, ticker: str) -> Quote | None:
        df = self.history(ticker, lookback_days=10, refresh=True)
        if df.empty:
            return None
        last = df.iloc[-1]
        currency = self.currency(ticker)
        return Quote(ticker=ticker, price=float(last["close"]), currency=currency,
                     asof=df.index[-1].to_pydatetime())

    def currency(self, ticker: str) -> str:
        if _HAS_YF:
            try:
                info = yf.Ticker(ticker).fast_info
                cur = getattr(info, "currency", None) or info.get("currency") if isinstance(info, dict) else None  # type: ignore[attr-defined]
                if cur:
                    return str(cur).upper()
            except Exception:
                pass
        if ticker.endswith(".SW"):
            return "CHF"
        if ticker.endswith(".DE") or ticker.endswith(".AS") or ticker.endswith(".PA"):
            return "EUR"
        if ticker.endswith(".L"):
            return "GBP"
        return "USD"

    # ---- FX ---------------------------------------------------------------
    def fx_to_chf(self, currency: str, fx_pairs: dict[str, str]) -> float:
        currency = currency.upper()
        if currency == "CHF":
            return 1.0
        pair = fx_pairs.get(currency)
        if not pair:
            log.warning("No FX pair configured for %s; assuming 1.0", currency)
            return 1.0
        df = self.history(pair, lookback_days=15, refresh=True)
        if df.empty:
            return 1.0
        return float(df["close"].dropna().iloc[-1])

    # ---- batch ------------------------------------------------------------
    def refresh_universe(self, tickers: Iterable[str], lookback_days: int = 504) -> dict:
        results: dict[str, int] = {}
        for t in tickers:
            df = self.history(t, lookback_days=lookback_days, refresh=True)
            results[t] = len(df)
        return results
