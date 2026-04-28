"""Flask dashboard.

Lightweight read-only UI exposing the contents of the SQLite store on a port.
Optionally runs a background observer thread so the dashboard self-refreshes
without an external systemd unit.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ..observer.orchestrator import Orchestrator
from ..utils.logging import get_logger

log = get_logger("investai.web")


def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


class DashboardState:
    """Thread-safe wrapper around an Orchestrator instance."""

    def __init__(self, orch: Orchestrator) -> None:
        self.orch = orch
        self.lock = threading.RLock()
        self.observer_thread: threading.Thread | None = None
        self.observer_running = False
        self.last_cycle_at: str | None = None
        self.last_cycle_error: str | None = None

    def cycle(self) -> dict:
        with self.lock:
            try:
                rep = self.orch.cycle()
                self.last_cycle_at = rep.timestamp
                self.last_cycle_error = None
                return {"ok": True, "timestamp": rep.timestamp,
                        "actions": rep.actions,
                        "forecasts": len(rep.forecasts),
                        "total_chf": rep.portfolio_total_chf,
                        "pnl_chf": rep.portfolio_pnl_chf,
                        "pnl_pct": rep.portfolio_pnl_pct}
            except Exception as e:
                log.exception("Dashboard cycle failed: %s", e)
                self.last_cycle_error = str(e)
                return {"ok": False, "error": str(e)}

    def start_observer(self, interval: int) -> None:
        if self.observer_running:
            return
        self.observer_running = True

        def _loop() -> None:
            import time
            while self.observer_running:
                self.cycle()
                for _ in range(interval):
                    if not self.observer_running:
                        return
                    time.sleep(1)

        t = threading.Thread(target=_loop, name="investai-observer", daemon=True)
        self.observer_thread = t
        t.start()
        log.info("Background observer started (interval=%ds)", interval)

    def stop_observer(self) -> None:
        self.observer_running = False


def create_app(*, db_path: Path | None = None,
               allow_synthetic: bool = False,
               background_observer: bool = False,
               observer_interval: int = 900) -> Flask:
    app = Flask(__name__,
                template_folder=str(Path(__file__).parent / "templates"),
                static_folder=str(Path(__file__).parent / "static"))
    orch = Orchestrator(db_path=db_path, allow_synthetic=allow_synthetic)
    state = DashboardState(orch)
    app.config["STATE"] = state
    if background_observer:
        state.start_observer(observer_interval)

    # ------------------------------------------------------------------
    @app.get("/")
    def index() -> Any:
        return render_template("dashboard.html")

    @app.get("/api/summary")
    def api_summary():
        snap = orch.portfolio.value()
        history = orch.store.portfolio_history()
        first = history[0] if history else None
        return jsonify({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cash_chf": snap.cash_chf,
            "holdings_chf": snap.holdings_value_chf,
            "total_chf": snap.total_value_chf,
            "starting_chf": snap.starting_cash_chf,
            "pnl_chf": snap.pnl_chf,
            "pnl_pct": snap.pnl_pct,
            "positions": snap.positions,
            "history_points": len(history),
            "first_history_ts": first["ts"] if first else None,
            "last_cycle_at": state.last_cycle_at,
            "last_cycle_error": state.last_cycle_error,
            "observer_running": state.observer_running,
            "universe_size": len(orch.universe.all_tickers),
        })

    @app.get("/api/history")
    def api_history():
        rows = orch.store.portfolio_history()
        return jsonify([{
            "ts": r["ts"],
            "total_chf": r["total_value_chf"],
            "cash_chf": r["cash_chf"],
            "holdings_chf": r["holdings_value_chf"],
            "pnl_chf": r["pnl_chf"],
            "pnl_pct": r["pnl_pct"],
        } for r in rows])

    @app.get("/api/trades")
    def api_trades():
        limit = int(request.args.get("limit", 50))
        rows = orch.store.list_trades()[-limit:][::-1]
        return jsonify([_row_to_dict(r) for r in rows])

    @app.get("/api/forecasts")
    def api_forecasts():
        limit = int(request.args.get("limit", 200))
        with orch.store.cursor() as cur:
            cur.execute(
                "SELECT ticker,made_on,target_date,horizon_days,expected_return,"
                "direction_prob,realized_return,error,model FROM predictions "
                "ORDER BY made_on DESC, ticker LIMIT ?", (limit,))
            rows = cur.fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @app.get("/api/forecasts/latest")
    def api_forecasts_latest():
        with orch.store.cursor() as cur:
            cur.execute(
                "SELECT p.* FROM predictions p "
                "JOIN (SELECT ticker, MAX(made_on) AS m FROM predictions GROUP BY ticker) x "
                "ON x.ticker=p.ticker AND x.m=p.made_on "
                "ORDER BY p.expected_return DESC")
            rows = cur.fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @app.get("/api/models")
    def api_models():
        with orch.store.cursor() as cur:
            cur.execute("SELECT model, params, score, metric, updated_at FROM model_params")
            params_rows = cur.fetchall()
            cur.execute(
                "SELECT evaluated_at, model, sample_size, mae, hit_rate, sharpe, params "
                "FROM model_performance ORDER BY evaluated_at DESC LIMIT 50")
            perf_rows = cur.fetchall()
        return jsonify({
            "champions": [_row_to_dict(r) for r in params_rows],
            "history": [_row_to_dict(r) for r in perf_rows],
        })

    @app.get("/api/universe")
    def api_universe():
        return jsonify({
            "base_currency": orch.universe.base_currency,
            "stocks": orch.universe.stocks,
            "indices": orch.universe.indices,
            "funds": orch.universe.funds,
        })

    # --- write actions -------------------------------------------------
    @app.post("/api/cycle")
    def api_cycle():
        return jsonify(state.cycle())

    @app.post("/api/optimize")
    def api_optimize():
        with state.lock:
            outcomes = orch.run_optimizer()
            return jsonify([{"model": o.model, "metric": o.metric,
                             "score": o.score, "params": o.params,
                             "samples": o.sample_size} for o in outcomes])

    @app.post("/api/portfolio/reset")
    def api_reset():
        if request.args.get("confirm") != "yes":
            return jsonify({"ok": False, "error": "missing confirm=yes"}), 400
        with state.lock:
            orch.portfolio.reset()
        return jsonify({"ok": True})

    @app.post("/api/observer/start")
    def api_observer_start():
        interval = int(request.args.get("interval", observer_interval))
        state.start_observer(interval)
        return jsonify({"running": state.observer_running, "interval": interval})

    @app.post("/api/observer/stop")
    def api_observer_stop():
        state.stop_observer()
        return jsonify({"running": state.observer_running})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def serve(*, host: str = "0.0.0.0", port: int = 8080,
          allow_synthetic: bool = False,
          background_observer: bool = False,
          observer_interval: int = 900) -> None:  # pragma: no cover
    from waitress import serve as wserve
    app = create_app(allow_synthetic=allow_synthetic,
                     background_observer=background_observer,
                     observer_interval=observer_interval)
    log.info("Dashboard listening on http://%s:%s", host, port)
    wserve(app, host=host, port=port, threads=4)
