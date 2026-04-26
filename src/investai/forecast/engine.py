"""Forecast engine.

Trains an ensemble of regressors on engineered features and produces a
probability-weighted expected return for each ticker. Per-model parameters are
loaded from the persistent store when the optimizer has already learned them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from ..analysis.indicators import feature_frame
from ..db.store import Store
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class Forecast:
    ticker: str
    made_on: str
    target_date: str
    horizon_days: int
    expected_return: float
    direction_prob: float
    model: str
    features: dict[str, float]


def _split_xy(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Return ``(X_train, y_train, X_latest)``.

    Drops rows lacking either features or the forward target. The most recent
    row (where target is NaN because the future hasn't happened) is kept aside
    as the prediction input.
    """
    feat_cols = [c for c in features.columns if c != "target_return"]
    cleaned = features.replace([np.inf, -np.inf], np.nan)
    train = cleaned.dropna()
    latest = cleaned[cleaned["target_return"].isna()].dropna(subset=feat_cols)
    if latest.empty:
        latest = cleaned.dropna(subset=feat_cols).tail(1)
    return train[feat_cols], train["target_return"], latest[feat_cols].tail(1)


def _build_models(params: dict[str, dict] | None) -> dict[str, Any]:
    p = params or {}
    return {
        "ridge": Ridge(**p.get("ridge", {"alpha": 1.0})),
        "rf": RandomForestRegressor(
            random_state=42, n_jobs=-1,
            **p.get("rf", {"n_estimators": 200, "max_depth": 6, "min_samples_leaf": 5})),
        "gbr": GradientBoostingRegressor(
            random_state=42,
            **p.get("gbr", {"n_estimators": 250, "max_depth": 3, "learning_rate": 0.05})),
    }


class ForecastEngine:
    def __init__(self, store: Store, *, horizon_days: int = 5,
                 lookback_days: int = 504, min_history_days: int = 120) -> None:
        self.store = store
        self.horizon_days = horizon_days
        self.lookback_days = lookback_days
        self.min_history_days = min_history_days

    # ------------------------------------------------------------------
    def _load_params(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for name in ("ridge", "rf", "gbr"):
            p = self.store.load_model_params(name)
            if p:
                out[name] = p
        return out

    # ------------------------------------------------------------------
    def predict(self, ticker: str, prices: pd.DataFrame) -> Forecast | None:
        if prices.empty or len(prices) < self.min_history_days:
            log.debug("Skip %s: insufficient history (%d rows)", ticker, len(prices))
            return None

        feats = feature_frame(prices.tail(self.lookback_days), horizon=self.horizon_days)
        x_train, y_train, x_latest = _split_xy(feats)
        if len(x_train) < 60 or x_latest.empty:
            log.debug("Skip %s: not enough training rows", ticker)
            return None

        scaler = StandardScaler()
        Xs = scaler.fit_transform(x_train)
        x_now = scaler.transform(x_latest)

        models = _build_models(self._load_params())
        preds: dict[str, float] = {}
        weights: dict[str, float] = {}
        for name, model in models.items():
            try:
                model.fit(Xs, y_train.values)
                pred = float(model.predict(x_now)[0])
                # In-sample R^2-like weight (clipped). Better than nothing as a prior.
                fitted = model.predict(Xs)
                ss_res = float(np.sum((y_train.values - fitted) ** 2))
                ss_tot = float(np.sum((y_train.values - y_train.mean()) ** 2)) or 1.0
                w = max(0.05, min(1.0, 1 - ss_res / ss_tot))
                preds[name] = pred
                weights[name] = w
            except Exception as e:
                log.warning("Model %s failed on %s: %s", name, ticker, e)

        if not preds:
            return None

        wsum = sum(weights.values())
        ensemble_log = sum(p * weights[m] for m, p in preds.items()) / wsum
        # Convert log-return target back to simple return for downstream use.
        expected_return = float(np.expm1(ensemble_log))

        # Directional probability via cross-model agreement weighted by sigma.
        sigma = float(y_train.std() or 1e-4)
        direction_prob = float(
            sum(weights[m] * (0.5 + 0.5 * np.tanh(p / sigma))
                for m, p in preds.items()) / wsum
        )

        target_date = (prices.index[-1] + pd.tseries.offsets.BDay(self.horizon_days)).date()
        feature_snapshot = {k: float(v) for k, v in x_latest.iloc[0].items()}
        return Forecast(
            ticker=ticker,
            made_on=prices.index[-1].date().isoformat(),
            target_date=target_date.isoformat(),
            horizon_days=self.horizon_days,
            expected_return=expected_return,
            direction_prob=direction_prob,
            model="ensemble",
            features=feature_snapshot,
        )

    # ------------------------------------------------------------------
    def persist(self, fc: Forecast) -> None:
        self.store.insert_prediction(
            ticker=fc.ticker, made_on=fc.made_on, target_date=fc.target_date,
            horizon_days=fc.horizon_days, expected_return=fc.expected_return,
            direction_prob=fc.direction_prob, model=fc.model,
            features=fc.features,
        )

    # ------------------------------------------------------------------
    def evaluate_pending(self, prices_by_ticker: dict[str, pd.DataFrame]) -> int:
        """Fill in realized returns for predictions whose target date is past."""
        today = datetime.utcnow().date().isoformat()
        rows = self.store.pending_predictions(today)
        n = 0
        for r in rows:
            df = prices_by_ticker.get(r["ticker"])
            if df is None or df.empty:
                continue
            try:
                made = pd.Timestamp(r["made_on"])
                target = pd.Timestamp(r["target_date"])
                p_made = float(df.loc[df.index <= made, "close"].iloc[-1])
                later = df.loc[df.index >= target, "close"]
                if later.empty:
                    continue
                p_target = float(later.iloc[0])
                realized = p_target / p_made - 1.0
                self.store.update_prediction_realized(r["id"], realized)
                n += 1
            except Exception as e:
                log.debug("Cannot resolve prediction %s: %s", r["id"], e)
        return n
