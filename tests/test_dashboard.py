from __future__ import annotations

import pytest

from investai.observer.orchestrator import Orchestrator
from investai.web.app import create_app


@pytest.fixture
def client(tmp_db, tiny_universe, fast_settings, monkeypatch):
    # Ensure create_app uses our small universe + settings.
    def _fake_orch(*a, **kw):
        return Orchestrator(db_path=tmp_db, settings=fast_settings,
                            universe=tiny_universe, allow_synthetic=True)
    monkeypatch.setattr("investai.web.app.Orchestrator", _fake_orch)
    app = create_app(db_path=tmp_db, allow_synthetic=True,
                     background_observer=False)
    return app.test_client()


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"InvestAI" in r.data


def test_summary_endpoint(client):
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.get_json()
    assert "total_chf" in body
    assert body["starting_chf"] == 10000.0
    assert body["observer_running"] is False
    assert body["universe_size"] >= 1


def test_history_and_trades_endpoints_empty(client):
    body = client.get("/api/history").get_json()
    assert body["history"] == []
    assert body["benchmark"] == []
    assert "benchmark_ticker" in body
    assert client.get("/api/trades").get_json() == []
    assert client.get("/api/forecasts").get_json() == []


def test_cycle_endpoint_runs(client):
    r = client.post("/api/cycle")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "total_chf" in body

    r2 = client.get("/api/summary")
    assert r2.get_json()["last_cycle_at"] == body["timestamp"]


def test_reset_requires_confirm(client):
    r = client.post("/api/portfolio/reset")
    assert r.status_code == 400
    r = client.post("/api/portfolio/reset?confirm=yes")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"ok": True}
