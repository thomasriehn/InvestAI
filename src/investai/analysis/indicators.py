"""Technical indicators and feature engineering."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    macd_line = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd_line, signal)
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line,
                         "macd_hist": macd_line - signal_line})


def bollinger(close: pd.Series, window: int = 20, k: float = 2.0) -> pd.DataFrame:
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = ma + k * std
    lower = ma - k * std
    width = (upper - lower) / ma
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"bb_ma": ma, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": width, "bb_pct_b": pct_b})


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(period).mean()


def feature_frame(df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """Build a feature matrix with a forward-looking ``target_return``.

    ``df`` is expected to have columns ``open, high, low, close, adj_close,
    volume`` indexed by date.
    """
    if df.empty:
        return df

    close = df["close"].astype(float)
    high = df["high"].astype(float).fillna(close)
    low = df["low"].astype(float).fillna(close)
    vol = df["volume"].astype(float).fillna(0.0)

    f = pd.DataFrame(index=df.index)
    f["close"] = close
    f["ret_1"] = close.pct_change(1)
    f["ret_5"] = close.pct_change(5)
    f["ret_10"] = close.pct_change(10)
    f["ret_21"] = close.pct_change(21)
    f["vol_20"] = f["ret_1"].rolling(20).std()
    f["vol_60"] = f["ret_1"].rolling(60).std()
    f["sma_5"] = close.rolling(5).mean() / close - 1
    f["sma_20"] = close.rolling(20).mean() / close - 1
    f["sma_50"] = close.rolling(50).mean() / close - 1
    f["sma_200"] = close.rolling(200).mean() / close - 1
    f["ema_12"] = _ema(close, 12) / close - 1
    f["ema_26"] = _ema(close, 26) / close - 1
    f["rsi_14"] = rsi(close, 14)
    f = pd.concat([f, macd(close), bollinger(close)], axis=1)
    f["atr_14"] = atr(high, low, close, 14) / close
    f["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)
    f["mom_63"] = close.pct_change(63)
    f["mom_126"] = close.pct_change(126)

    # Forward target: log-return over horizon (more symmetric than raw pct).
    fwd = np.log(close.shift(-horizon) / close)
    f["target_return"] = fwd
    return f


def latest_feature_row(df: pd.DataFrame, horizon: int = 5) -> pd.Series | None:
    """Return the most recent feature row whose features are fully observed
    (target may be NaN — that is intended for live prediction)."""
    f = feature_frame(df, horizon=horizon)
    if f.empty:
        return None
    cols = [c for c in f.columns if c != "target_return"]
    f2 = f[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if f2.empty:
        return None
    return f2.iloc[-1]
