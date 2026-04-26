from __future__ import annotations

import math

import numpy as np
import pandas as pd

from investai.backtest.runner import walk_forward
from investai.data.market import MarketData
from investai.db.store import Store
from investai.forecast.engine import ForecastEngine


def _seed_history(store: Store, ticker: str, n: int = 500) -> pd.DataFrame:
    md = MarketData(store, allow_synthetic=True)
    return md.history(ticker, lookback_days=n, refresh=True)


def test_forecast_predict_returns_object(tmp_db):
    store = Store(tmp_db)
    df = _seed_history(store, "TEST.X", n=500)
    eng = ForecastEngine(store, horizon_days=5, lookback_days=400, min_history_days=100)
    fc = eng.predict("TEST.X", df)
    assert fc is not None
    assert math.isfinite(fc.expected_return)
    assert 0.0 <= fc.direction_prob <= 1.0
    assert fc.target_date > fc.made_on


def test_walk_forward_runs(tmp_db):
    store = Store(tmp_db)
    df = _seed_history(store, "BT.X", n=600)
    res = walk_forward(df, params={"kind": "ridge", "alpha": 1.0},
                       horizon=5, folds=3, train_min=120)
    assert res.samples > 0
    assert math.isfinite(res.mae)
    assert 0 <= res.hit_rate <= 1


def test_evaluate_pending_fills_realized(tmp_db):
    store = Store(tmp_db)
    df = _seed_history(store, "RES.X", n=500)
    eng = ForecastEngine(store, horizon_days=5, lookback_days=400, min_history_days=100)

    # Insert a fake prediction whose target lies inside the cached range.
    made = df.index[-30].date().isoformat()
    target = df.index[-25].date().isoformat()
    store.insert_prediction(ticker="RES.X", made_on=made, target_date=target,
                            horizon_days=5, expected_return=0.0,
                            direction_prob=0.5, model="ensemble", features={})
    n = eng.evaluate_pending({"RES.X": df})
    assert n == 1
    rows = store.predictions_for_evaluation()
    assert rows and rows[0]["realized_return"] is not None
