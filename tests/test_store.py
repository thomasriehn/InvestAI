from __future__ import annotations

from datetime import datetime, timezone

from investai.db.store import Store


def test_init_and_portfolio_lifecycle(tmp_db):
    store = Store(tmp_db)
    assert store.get_portfolio() is None
    ts = datetime.now(timezone.utc).isoformat()
    store.init_portfolio(10000.0, ts)
    pf = store.get_portfolio()
    assert pf is not None
    assert pf["cash_chf"] == 10000.0


def test_position_upsert_and_delete(tmp_db):
    store = Store(tmp_db)
    store.init_portfolio(10000.0, "2026-01-01")
    store.upsert_position("AAPL", 5, 100.0, "USD")
    rows = store.get_positions()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    store.upsert_position("AAPL", 0.0, 0.0, "USD")
    assert store.get_positions() == []


def test_predictions_pending_then_resolved(tmp_db):
    store = Store(tmp_db)
    store.insert_prediction(ticker="AAPL", made_on="2026-01-01",
                            target_date="2026-01-08", horizon_days=5,
                            expected_return=0.02, direction_prob=0.6,
                            model="ensemble", features={"a": 1.0})
    pending = store.pending_predictions("2026-01-09")
    assert len(pending) == 1
    pid = pending[0]["id"]
    store.update_prediction_realized(pid, 0.025)
    resolved = store.predictions_for_evaluation()
    assert len(resolved) == 1
    assert resolved[0]["realized_return"] == 0.025
    assert abs(resolved[0]["error"] - (0.025 - 0.02)) < 1e-9


def test_model_params_roundtrip(tmp_db):
    store = Store(tmp_db)
    store.save_model_params("rf", {"n_estimators": 200}, 0.5, "sharpe", "now")
    assert store.load_model_params("rf") == {"n_estimators": 200}
    assert store.load_model_params("missing") is None
