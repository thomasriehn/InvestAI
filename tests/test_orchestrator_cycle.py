from __future__ import annotations

from investai.observer.orchestrator import Orchestrator


def test_cycle_runs_end_to_end(tmp_db, tiny_universe, fast_settings):
    orch = Orchestrator(db_path=tmp_db, settings=fast_settings,
                        universe=tiny_universe, allow_synthetic=True)
    rep = orch.cycle()
    assert rep.portfolio_total_chf > 0
    # Some forecasts should be produced after enough synthetic history is built up.
    # Run a second cycle to make sure the loop is idempotent.
    rep2 = orch.cycle()
    assert rep2.portfolio_total_chf > 0


def test_optimizer_persists_params(tmp_db, tiny_universe, fast_settings):
    orch = Orchestrator(db_path=tmp_db, settings=fast_settings,
                        universe=tiny_universe, allow_synthetic=True)
    # warm up history first
    orch._refresh_universe()
    outcomes = orch.run_optimizer()
    assert outcomes  # at least one model optimised
    for o in outcomes:
        assert orch.store.load_model_params(o.model) == o.params
