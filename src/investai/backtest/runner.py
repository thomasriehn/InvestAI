"""Walk-forward backtester.

Re-creates the predict → trade → mark-to-market loop on historical data so we
can measure model quality and feed the optimizer with a real performance
signal. Operates entirely off cached prices, no network required.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from ..analysis.indicators import feature_frame


@dataclass
class BacktestResult:
    samples: int
    mae: float
    rmse: float
    hit_rate: float
    sharpe: float
    cumulative_return: float
    params: dict


def _model_from_params(params: dict):
    kind = params.get("kind", "gbr")
    p = {k: v for k, v in params.items() if k != "kind"}
    if kind == "ridge":
        return Ridge(**p)
    if kind == "rf":
        return RandomForestRegressor(random_state=42, n_jobs=-1, **p)
    return GradientBoostingRegressor(random_state=42, **p)


def walk_forward(prices: pd.DataFrame, *, params: dict, horizon: int = 5,
                 folds: int = 4, train_min: int = 180) -> BacktestResult:
    """Run an expanding-window walk-forward evaluation.

    Returns predictions vs realised returns plus a simple long-only PnL using
    a +1.5% / -1.0% threshold rule (same as the live policy).
    """
    feats = feature_frame(prices, horizon=horizon).replace([np.inf, -np.inf], np.nan)
    feats = feats.dropna(subset=[c for c in feats.columns if c != "target_return"])
    train_full = feats.dropna()
    if len(train_full) < train_min + folds * 5:
        return BacktestResult(0, math.nan, math.nan, math.nan, math.nan, math.nan, params)

    feat_cols = [c for c in feats.columns if c != "target_return"]
    n = len(train_full)
    fold_size = (n - train_min) // folds
    if fold_size <= 0:
        return BacktestResult(0, math.nan, math.nan, math.nan, math.nan, math.nan, params)

    preds: list[float] = []
    actuals: list[float] = []
    for k in range(folds):
        train_end = train_min + k * fold_size
        test_end = min(n, train_end + fold_size)
        train = train_full.iloc[:train_end]
        test = train_full.iloc[train_end:test_end]
        if test.empty:
            break
        scaler = StandardScaler()
        Xtr = scaler.fit_transform(train[feat_cols])
        Xte = scaler.transform(test[feat_cols])
        model = _model_from_params(params)
        model.fit(Xtr, train["target_return"].values)
        yhat = model.predict(Xte)
        preds.extend(yhat.tolist())
        actuals.extend(test["target_return"].values.tolist())

    if not preds:
        return BacktestResult(0, math.nan, math.nan, math.nan, math.nan, math.nan, params)

    preds_a = np.array(preds)
    actuals_a = np.array(actuals)
    # Targets are log-returns; convert before evaluation for interpretability.
    preds_simple = np.expm1(preds_a)
    actuals_simple = np.expm1(actuals_a)

    err = preds_simple - actuals_simple
    mae = float(np.mean(np.abs(err)))
    rmse = float(math.sqrt(np.mean(err * err)))
    hit_rate = float(np.mean(np.sign(preds_simple) == np.sign(actuals_simple)))

    # Strategy PnL: take the realized return whenever forecast crosses thresholds.
    signal = np.where(preds_simple >= 0.015, 1.0,
                      np.where(preds_simple <= -0.01, -0.5, 0.0))
    strat = signal * actuals_simple
    if strat.std(ddof=0) > 0:
        ann = (252 / horizon) ** 0.5
        sharpe = float(strat.mean() / strat.std(ddof=0) * ann)
    else:
        sharpe = 0.0
    cum = float(np.prod(1 + strat) - 1)
    return BacktestResult(len(preds), mae, rmse, hit_rate, sharpe, cum, params)
