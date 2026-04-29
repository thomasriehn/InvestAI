from __future__ import annotations

import numpy as np
import pandas as pd

from investai.analysis.indicators import feature_frame, latest_feature_row, rsi


def _synthetic_prices(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rets = rng.normal(0.0005, 0.01, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                         "adj_close": close, "volume": rng.integers(1e5, 1e6, n)},
                        index=dates)


def test_rsi_bounds():
    s = pd.Series(np.linspace(1, 100, 250))
    r = rsi(s, 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_feature_frame_columns():
    df = _synthetic_prices()
    f = feature_frame(df, horizon=5)
    expected_subset = {"close", "ret_1", "rsi_14", "macd", "bb_width",
                       "atr_14", "target_return"}
    assert expected_subset.issubset(f.columns)


def test_latest_feature_row_present():
    df = _synthetic_prices()
    row = latest_feature_row(df, horizon=5)
    assert row is not None
    assert "close" in row.index
    assert not np.isnan(row["rsi_14"])
