from __future__ import annotations

import pandas as pd

from investai.data.market import MarketData
from investai.db.store import Store
from investai.forecast.engine import Forecast
from investai.portfolio.engine import PortfolioEngine


def _setup(tmp_db, fast_settings):
    store = Store(tmp_db)
    md = MarketData(store, allow_synthetic=True)
    # Seed history for each ticker so quote() works.
    for t in ("AAPL", "NESN.SW"):
        md.history(t, lookback_days=200, refresh=True)
    md.history("CHF=X", lookback_days=200, refresh=True)
    return store, md


def test_buy_then_value(tmp_db, fast_settings):
    store, md = _setup(tmp_db, fast_settings)
    eng = PortfolioEngine(store, md, {"USD": "CHF=X"}, fast_settings.portfolio)

    forecasts = [
        Forecast(ticker="AAPL", made_on="2026-01-01", target_date="2026-01-08",
                 horizon_days=5, expected_return=0.05, direction_prob=0.8,
                 model="ensemble", features={}),
        Forecast(ticker="NESN.SW", made_on="2026-01-01", target_date="2026-01-08",
                 horizon_days=5, expected_return=0.04, direction_prob=0.75,
                 model="ensemble", features={}),
    ]
    actions = eng.apply_forecasts(forecasts, fast_settings.forecast)
    assert any(a["action"] == "BUY" and a["status"] == "executed" for a in actions)
    snap = eng.value()
    assert snap.total_value_chf > 0
    assert snap.starting_cash_chf == 10000.0
    assert snap.cash_chf < 10000.0  # we spent some cash
    assert len(snap.positions) >= 1


def test_sell_closes_position(tmp_db, fast_settings):
    store, md = _setup(tmp_db, fast_settings)
    eng = PortfolioEngine(store, md, {"USD": "CHF=X"}, fast_settings.portfolio)

    eng.apply_forecasts([
        Forecast(ticker="AAPL", made_on="2026-01-01", target_date="2026-01-08",
                 horizon_days=5, expected_return=0.05, direction_prob=0.8,
                 model="ensemble", features={}),
    ], fast_settings.forecast)
    assert any(p["ticker"] == "AAPL" for p in eng.value().positions)

    eng.apply_forecasts([
        Forecast(ticker="AAPL", made_on="2026-01-02", target_date="2026-01-09",
                 horizon_days=5, expected_return=-0.05, direction_prob=0.2,
                 model="ensemble", features={}),
    ], fast_settings.forecast)
    assert not any(p["ticker"] == "AAPL" for p in eng.value().positions)


def test_position_cap_respected(tmp_db, fast_settings):
    store, md = _setup(tmp_db, fast_settings)
    eng = PortfolioEngine(store, md, {"USD": "CHF=X"}, fast_settings.portfolio)
    forecasts = [
        Forecast(ticker="AAPL", made_on="2026-01-01", target_date="2026-01-08",
                 horizon_days=5, expected_return=0.10, direction_prob=0.95,
                 model="ensemble", features={}),
    ] * 6   # repeated buys must not exceed the 25% cap
    eng.apply_forecasts(forecasts, fast_settings.forecast)
    snap = eng.value()
    aapl = next(p for p in snap.positions if p["ticker"] == "AAPL")
    weight = aapl["market_value_chf"] / snap.total_value_chf
    assert weight <= fast_settings.portfolio["max_position_pct"] + 0.05
