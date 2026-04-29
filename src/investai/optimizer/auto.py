"""Automatic hyper-parameter search driven by walk-forward backtests.

Each search picks the best model+params combo against a multi-ticker panel
and persists them to ``model_params``. Subsequent forecasts then load the
optimal config automatically — that's what closes the self-optimisation loop.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from ..backtest.runner import walk_forward
from ..db.store import Store
from ..utils.logging import get_logger

log = get_logger(__name__)


_RIDGE_SPACE = {
    "kind": ["ridge"],
    "alpha": [0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
}
_RF_SPACE = {
    "kind": ["rf"],
    "n_estimators": [100, 200, 400],
    "max_depth": [3, 4, 6, 8, None],
    "min_samples_leaf": [2, 5, 10],
    "max_features": ["sqrt", 0.5, 1.0],
}
_GBR_SPACE = {
    "kind": ["gbr"],
    "n_estimators": [100, 200, 400],
    "max_depth": [2, 3, 4],
    "learning_rate": [0.02, 0.05, 0.1],
    "subsample": [0.6, 0.8, 1.0],
}


def _sample(space: dict, rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in space.items()}


@dataclass
class OptimizationOutcome:
    model: str
    score: float
    metric: str
    params: dict
    sample_size: int


class AutoOptimizer:
    def __init__(self, store: Store, *, iterations: int = 24, metric: str = "sharpe",
                 folds: int = 4, horizon_days: int = 5, seed: int = 1234) -> None:
        self.store = store
        self.iterations = iterations
        self.metric = metric
        self.folds = folds
        self.horizon = horizon_days
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    def _score(self, result) -> float:
        if result.samples == 0:
            return -math.inf
        if self.metric == "mae":
            return -float(result.mae)
        if self.metric == "hit_rate":
            return float(result.hit_rate)
        return float(result.sharpe)

    # ------------------------------------------------------------------
    def _evaluate(self, params: dict, panel: list[pd.DataFrame]) -> tuple[float, int]:
        scores, samples = [], 0
        for prices in panel:
            r = walk_forward(prices, params=params, horizon=self.horizon, folds=self.folds)
            if r.samples == 0:
                continue
            scores.append(self._score(r))
            samples += r.samples
        if not scores:
            return -math.inf, 0
        return float(sum(scores) / len(scores)), samples

    # ------------------------------------------------------------------
    def optimise(self, prices_by_ticker: dict[str, pd.DataFrame]) -> list[OptimizationOutcome]:
        panel = [df for df in prices_by_ticker.values() if not df.empty and len(df) > 200]
        if not panel:
            log.warning("No usable history for optimisation")
            return []

        outcomes: list[OptimizationOutcome] = []
        for name, space in (("ridge", _RIDGE_SPACE), ("rf", _RF_SPACE), ("gbr", _GBR_SPACE)):
            best_score = -math.inf
            best_params: dict | None = None
            best_samples = 0
            for _ in range(self.iterations):
                params = _sample(space, self._rng)
                score, n = self._evaluate(params, panel)
                if score > best_score:
                    best_score = score
                    best_params = {k: v for k, v in params.items() if k != "kind"}
                    best_samples = n
            if best_params is None or best_score == -math.inf:
                continue
            ts = datetime.now(timezone.utc).isoformat()
            self.store.save_model_params(name, best_params, best_score, self.metric, ts)
            self.store.insert_model_perf(
                evaluated_at=ts, model=name, ticker=None,
                horizon_days=self.horizon, sample_size=best_samples,
                sharpe=best_score if self.metric == "sharpe" else None,
                hit_rate=best_score if self.metric == "hit_rate" else None,
                mae=-best_score if self.metric == "mae" else None,
                params=best_params,
            )
            outcomes.append(OptimizationOutcome(
                model=name, score=best_score, metric=self.metric,
                params=best_params, sample_size=best_samples,
            ))
            log.info("Optimised %s — %s=%.4f  params=%s", name, self.metric,
                     best_score, best_params)
        return outcomes

    # ------------------------------------------------------------------
    @staticmethod
    def from_panel(store: Store, prices: dict[str, pd.DataFrame],
                   cfg: dict, horizon_days: int) -> Iterable[OptimizationOutcome]:
        opt = AutoOptimizer(
            store,
            iterations=int(cfg.get("search_iterations", 24)),
            metric=str(cfg.get("metric", "sharpe")),
            folds=int(cfg.get("walk_forward_folds", 4)),
            horizon_days=horizon_days,
        )
        return opt.optimise(prices)
