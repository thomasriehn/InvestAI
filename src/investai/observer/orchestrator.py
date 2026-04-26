"""Multi-agent orchestrator and non-stop market observer."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..data.market import MarketData
from ..db.store import Store, DEFAULT_DB_PATH
from ..forecast.engine import ForecastEngine, Forecast
from ..optimizer.auto import AutoOptimizer
from ..portfolio.engine import PortfolioEngine
from ..utils.config import Settings, Universe, load_settings, load_universe
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class CycleReport:
    timestamp: str
    refreshed: dict[str, int]
    forecasts: list[Forecast]
    actions: list[dict]
    portfolio_total_chf: float
    portfolio_pnl_chf: float
    portfolio_pnl_pct: float
    resolved_predictions: int
    optimised_models: list[dict] = field(default_factory=list)


class Orchestrator:
    """Glues the agents together. Each ``cycle()`` is one observation tick."""

    def __init__(self, *, db_path: Path | None = None,
                 settings: Settings | None = None,
                 universe: Universe | None = None,
                 allow_synthetic: bool = True) -> None:
        self.store = Store(db_path or DEFAULT_DB_PATH)
        self.settings = settings or load_settings()
        self.universe = universe or load_universe()
        self.market = MarketData(self.store, allow_synthetic=allow_synthetic)
        self.forecaster = ForecastEngine(
            self.store,
            horizon_days=int(self.settings.forecast.get("horizon_days", 5)),
            lookback_days=int(self.settings.forecast.get("lookback_days", 504)),
            min_history_days=int(self.settings.forecast.get("min_history_days", 120)),
        )
        self.portfolio = PortfolioEngine(self.store, self.market,
                                         self.universe.fx_pairs, self.settings.portfolio)
        self._cycles_since_optim = 0

    # ------------------------------------------------------------------
    def _refresh_universe(self) -> dict[str, int]:
        # FX pairs first, so portfolio valuation works.
        for pair in self.universe.fx_pairs.values():
            self.market.history(pair, lookback_days=120, refresh=True)
        return self.market.refresh_universe(
            self.universe.all_tickers,
            lookback_days=int(self.settings.forecast.get("lookback_days", 504)))

    def _all_prices(self) -> dict[str, pd.DataFrame]:
        return {t: self.market.history(t, lookback_days=int(
            self.settings.forecast.get("lookback_days", 504)),
            refresh=False) for t in self.universe.all_tickers}

    # ------------------------------------------------------------------
    def cycle(self) -> CycleReport:
        ts = datetime.now(timezone.utc).isoformat()
        log.info("=== Cycle %s ===", ts)

        # 1. Data agent
        refreshed = self._refresh_universe()
        prices = self._all_prices()

        # 2. Resolve any predictions whose target date has now passed
        resolved = self.forecaster.evaluate_pending(prices)

        # 3. Forecast agent
        forecasts: list[Forecast] = []
        for ticker, df in prices.items():
            fc = self.forecaster.predict(ticker, df)
            if fc:
                self.forecaster.persist(fc)
                forecasts.append(fc)

        # 4. Portfolio agent
        actions = self.portfolio.apply_forecasts(forecasts, self.settings.forecast)
        snap = self.portfolio.record_history()

        # 5. Periodic optimisation
        optim_dump: list[dict] = []
        every = int(self.settings.observer.get("reoptimize_every_runs", 24))
        self._cycles_since_optim += 1
        if every > 0 and self._cycles_since_optim >= every:
            self._cycles_since_optim = 0
            optim_dump = [o.__dict__ for o in self.run_optimizer(prices)]

        return CycleReport(
            timestamp=ts, refreshed=refreshed, forecasts=forecasts, actions=actions,
            portfolio_total_chf=snap.total_value_chf,
            portfolio_pnl_chf=snap.pnl_chf, portfolio_pnl_pct=snap.pnl_pct,
            resolved_predictions=resolved, optimised_models=optim_dump,
        )

    # ------------------------------------------------------------------
    def run_optimizer(self, prices: dict[str, pd.DataFrame] | None = None):
        prices = prices if prices is not None else self._all_prices()
        return list(AutoOptimizer.from_panel(
            self.store, prices, self.settings.optimizer,
            horizon_days=int(self.settings.forecast.get("horizon_days", 5)),
        ))

    # ------------------------------------------------------------------
    def watch(self, *, max_cycles: int | None = None,
              poll_seconds: int | None = None) -> None:
        """Run forever (or for a fixed number of cycles, useful for tests)."""
        interval = poll_seconds or int(self.settings.observer.get(
            "poll_interval_seconds", 900))
        n = 0
        while True:
            try:
                self.cycle()
            except Exception as e:  # never let one bad cycle kill the loop
                log.exception("Cycle failed: %s", e)
            n += 1
            if max_cycles is not None and n >= max_cycles:
                return
            time.sleep(interval)
