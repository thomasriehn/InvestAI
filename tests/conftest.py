from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "investai.sqlite"


@pytest.fixture
def tiny_universe(tmp_path):
    """Small universe (3 stocks, 1 index, 1 fund) so tests stay fast."""
    from investai.utils.config import Universe
    return Universe(
        base_currency="CHF",
        stocks=["NESN.SW", "AAPL", "MSFT"],
        indices=["^SSMI"],
        funds=["VWRL.SW"],
        fx_pairs={"USD": "CHF=X", "EUR": "EURCHF=X"},
    )


@pytest.fixture
def fast_settings():
    from investai.utils.config import Settings
    return Settings(
        portfolio={"starting_cash_chf": 10000.0, "max_position_pct": 0.25,
                   "min_trade_chf": 100.0, "cash_reserve_pct": 0.05,
                   "trading_fee_pct": 0.001, "trading_fee_min_chf": 1.0},
        forecast={"horizon_days": 5, "lookback_days": 400,
                  "min_history_days": 80, "buy_threshold": 0.005,
                  "sell_threshold": -0.005, "confidence_floor": 0.5},
        observer={"poll_interval_seconds": 1, "off_hours_interval_seconds": 1,
                  "reoptimize_every_runs": 99},
        optimizer={"search_iterations": 3, "metric": "sharpe",
                   "walk_forward_folds": 3},
    )
