"""Command-line entry point."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..backtest.runner import walk_forward
from ..observer.orchestrator import Orchestrator
from ..utils.logging import get_logger

app = typer.Typer(help="InvestAI — autonomous market analysis & demo portfolio agent")
data_app = typer.Typer(help="Market data operations")
forecast_app = typer.Typer(help="Forecast operations")
portfolio_app = typer.Typer(help="Demo portfolio operations")
backtest_app = typer.Typer(help="Backtesting operations")
optimize_app = typer.Typer(help="Hyper-parameter optimisation")

app.add_typer(data_app, name="data")
app.add_typer(forecast_app, name="forecast")
app.add_typer(portfolio_app, name="portfolio")
app.add_typer(backtest_app, name="backtest")
app.add_typer(optimize_app, name="optimize")

console = Console()
log = get_logger("investai.cli")


def _orchestrator(synthetic: bool = False) -> Orchestrator:
    return Orchestrator(allow_synthetic=synthetic)


# =====================================================================
# data
# =====================================================================
@data_app.command("refresh")
def data_refresh(
    synthetic: bool = typer.Option(
        False, "--synthetic/--no-synthetic",
        help="Allow synthetic price fallback when network is unavailable."),
):
    """Download / refresh historical prices for the entire universe."""
    orch = _orchestrator(synthetic=synthetic)
    refreshed = orch._refresh_universe()
    table = Table(title="Refreshed tickers")
    table.add_column("ticker"); table.add_column("rows", justify="right")
    for t, n in refreshed.items():
        table.add_row(t, str(n))
    console.print(table)


@data_app.command("status")
def data_status():
    """Show how many bars are cached per ticker."""
    orch = _orchestrator()
    table = Table(title="Cache status")
    table.add_column("ticker"); table.add_column("kind")
    table.add_column("bars", justify="right"); table.add_column("last bar")
    for t in orch.universe.all_tickers:
        df = orch.market.history(t, refresh=False)
        last = df.index[-1].date().isoformat() if not df.empty else "—"
        table.add_row(t, orch.universe.kind(t), str(len(df)), last)
    console.print(table)


# =====================================================================
# forecast
# =====================================================================
@forecast_app.command("run")
def forecast_run(synthetic: bool = typer.Option(False, "--synthetic/--no-synthetic")):
    """Generate forecasts for the entire universe and persist them."""
    orch = _orchestrator(synthetic=synthetic)
    prices = orch._all_prices()
    resolved = orch.forecaster.evaluate_pending(prices)
    rows: list[dict] = []
    for t, df in prices.items():
        fc = orch.forecaster.predict(t, df)
        if fc:
            orch.forecaster.persist(fc)
            rows.append({"ticker": t, "expected_return": fc.expected_return,
                         "direction_prob": fc.direction_prob,
                         "target_date": fc.target_date})
    rows.sort(key=lambda r: r["expected_return"], reverse=True)
    table = Table(title=f"Forecasts (resolved {resolved} prior predictions)")
    for c in ("ticker", "expected_return", "direction_prob", "target_date"):
        table.add_column(c)
    for r in rows:
        table.add_row(r["ticker"], f"{r['expected_return']:+.2%}",
                      f"{r['direction_prob']:.0%}", r["target_date"])
    console.print(table)


@forecast_app.command("show")
def forecast_show(ticker: str):
    """Show recent forecasts for a single ticker."""
    orch = _orchestrator()
    with orch.store.cursor() as cur:
        cur.execute("SELECT * FROM predictions WHERE ticker=? ORDER BY made_on DESC LIMIT 20",
                    (ticker,))
        rows = cur.fetchall()
    table = Table(title=f"Predictions — {ticker}")
    for c in ("made_on", "target_date", "expected_return", "direction_prob",
              "realized_return", "error"):
        table.add_column(c)
    for r in rows:
        table.add_row(
            r["made_on"], r["target_date"],
            f"{r['expected_return']:+.2%}", f"{r['direction_prob']:.0%}",
            f"{r['realized_return']:+.2%}" if r["realized_return"] is not None else "—",
            f"{r['error']:+.2%}" if r["error"] is not None else "—",
        )
    console.print(table)


# =====================================================================
# portfolio
# =====================================================================
@portfolio_app.command("show")
def portfolio_show(json_out: bool = typer.Option(False, "--json")):
    """Show current portfolio snapshot."""
    orch = _orchestrator()
    snap = orch.portfolio.value()
    if json_out:
        console.print_json(json.dumps(snap.__dict__, default=str))
        return
    console.print(f"[bold]Total[/bold]: {snap.total_value_chf:,.2f} CHF "
                  f"(cash {snap.cash_chf:,.2f}, holdings {snap.holdings_value_chf:,.2f})")
    console.print(f"[bold]PnL vs {snap.starting_cash_chf:,.0f} CHF[/bold]: "
                  f"{snap.pnl_chf:+,.2f} CHF ({snap.pnl_pct:+.2%})")
    if snap.positions:
        table = Table(title="Positions")
        for c in ("ticker", "qty", "ccy", "price", "fx", "MV CHF", "u-PnL %"):
            table.add_column(c)
        for p in snap.positions:
            table.add_row(p["ticker"], f"{p['quantity']:.4f}", p["currency"],
                          f"{p['price_native']:.2f}", f"{p['fx_rate']:.4f}",
                          f"{p['market_value_chf']:,.2f}",
                          f"{p['unrealized_pnl_pct']:+.2%}")
        console.print(table)


@portfolio_app.command("rebalance")
def portfolio_rebalance(synthetic: bool = typer.Option(False, "--synthetic/--no-synthetic")):
    """Generate forecasts and apply BUY/SELL decisions to the demo portfolio."""
    orch = _orchestrator(synthetic=synthetic)
    rep = orch.cycle()
    table = Table(title="Trades executed")
    for c in ("ticker", "action", "status", "qty", "gross_chf", "fee_chf"):
        table.add_column(c)
    for a in rep.actions:
        table.add_row(a.get("ticker", ""), a.get("action", ""), a.get("status", ""),
                      f"{a.get('qty', 0):.4f}",
                      f"{a.get('gross_chf', 0):,.2f}",
                      f"{a.get('fee_chf', 0):,.2f}")
    console.print(table)
    console.print(f"Portfolio total: {rep.portfolio_total_chf:,.2f} CHF "
                  f"(PnL {rep.portfolio_pnl_chf:+,.2f}, {rep.portfolio_pnl_pct:+.2%})")


@portfolio_app.command("history")
def portfolio_history(limit: int = typer.Option(30, help="Number of points to show")):
    """Show portfolio equity curve."""
    orch = _orchestrator()
    rows = orch.store.portfolio_history()[-limit:]
    table = Table(title="Equity curve")
    for c in ("ts", "total CHF", "cash CHF", "holdings CHF", "PnL CHF", "PnL %"):
        table.add_column(c)
    for r in rows:
        table.add_row(r["ts"], f"{r['total_value_chf']:,.2f}", f"{r['cash_chf']:,.2f}",
                      f"{r['holdings_value_chf']:,.2f}", f"{r['pnl_chf']:+,.2f}",
                      f"{r['pnl_pct']:+.2%}")
    console.print(table)


@portfolio_app.command("trades")
def portfolio_trades(limit: int = typer.Option(20)):
    """List recent trades."""
    orch = _orchestrator()
    rows = orch.store.list_trades()[-limit:]
    table = Table(title=f"Last {len(rows)} trades")
    for c in ("ts", "ticker", "side", "qty", "price_chf", "fee_chf", "rationale"):
        table.add_column(c)
    for r in rows:
        table.add_row(r["ts"], r["ticker"], r["side"], f"{r['quantity']:.4f}",
                      f"{r['price_chf']:.2f}", f"{r['fee_chf']:.2f}",
                      r["rationale"] or "")
    console.print(table)


@portfolio_app.command("reset")
def portfolio_reset(yes: bool = typer.Option(False, "--yes", help="Confirm reset")):
    """Reset the demo portfolio back to the starting cash."""
    if not yes:
        console.print("[red]Refusing to reset without --yes[/red]")
        raise typer.Exit(code=1)
    orch = _orchestrator()
    orch.portfolio.reset()
    console.print("[green]Portfolio reset.[/green]")


# =====================================================================
# backtest
# =====================================================================
@backtest_app.command("run")
def backtest_run(
    folds: int = typer.Option(4),
    horizon: int = typer.Option(5),
    metric: str = typer.Option("sharpe"),
):
    """Walk-forward backtest the default model spaces against the cached panel."""
    orch = _orchestrator()
    prices = orch._all_prices()
    grids = {
        "ridge": [{"kind": "ridge", "alpha": a} for a in (0.5, 1.0, 5.0)],
        "rf": [{"kind": "rf", "n_estimators": 200, "max_depth": d, "min_samples_leaf": 5}
               for d in (4, 6, 8)],
        "gbr": [{"kind": "gbr", "n_estimators": 200, "max_depth": 3,
                 "learning_rate": lr} for lr in (0.03, 0.05, 0.1)],
    }
    table = Table(title=f"Walk-forward backtest (metric={metric}, folds={folds})")
    for c in ("model", "params", "samples", "MAE", "hit", "Sharpe", "cumret"):
        table.add_column(c)
    for name, candidates in grids.items():
        best = None
        for params in candidates:
            scores = []
            samples = 0
            best_per = None
            for df in prices.values():
                if df.empty:
                    continue
                r = walk_forward(df, params=params, horizon=horizon, folds=folds)
                if r.samples == 0:
                    continue
                scores.append(r)
                samples += r.samples
                best_per = r
            if not scores:
                continue
            avg_sharpe = sum(s.sharpe for s in scores) / len(scores)
            avg_hit = sum(s.hit_rate for s in scores) / len(scores)
            avg_mae = sum(s.mae for s in scores) / len(scores)
            avg_cum = sum(s.cumulative_return for s in scores) / len(scores)
            score = avg_sharpe if metric == "sharpe" else (
                avg_hit if metric == "hit_rate" else -avg_mae)
            if best is None or score > best[0]:
                best = (score, params, samples, avg_mae, avg_hit, avg_sharpe, avg_cum)
        if best:
            score, params, samples, mae, hit, sharpe, cum = best
            table.add_row(name, json.dumps({k: v for k, v in params.items() if k != 'kind'}),
                          str(samples), f"{mae:.4f}", f"{hit:.2%}",
                          f"{sharpe:+.3f}", f"{cum:+.2%}")
    console.print(table)


# =====================================================================
# optimize
# =====================================================================
@optimize_app.command("run")
def optimize_run(metric: str = typer.Option("sharpe")):
    """Run the auto-optimiser and persist the best params per model."""
    orch = _orchestrator()
    if metric:
        orch.settings.optimizer["metric"] = metric
    outcomes = orch.run_optimizer()
    table = Table(title="Optimisation result")
    for c in ("model", "metric", "score", "samples", "params"):
        table.add_column(c)
    for o in outcomes:
        table.add_row(o.model, o.metric, f"{o.score:+.4f}",
                      str(o.sample_size), json.dumps(o.params))
    console.print(table)


# =====================================================================
# watch
# =====================================================================
@app.command("watch")
def watch(
    cycles: int = typer.Option(0, help="0 means run forever"),
    interval: int = typer.Option(900, help="Seconds between cycles"),
    synthetic: bool = typer.Option(False, "--synthetic/--no-synthetic"),
):
    """Run the non-stop market observer."""
    orch = _orchestrator(synthetic=synthetic)
    log.info("Starting observer (interval=%ss, cycles=%s)",
             interval, cycles if cycles > 0 else "∞")
    orch.watch(max_cycles=cycles or None, poll_seconds=interval)


@app.command("info")
def info():
    """Show project info."""
    orch = _orchestrator()
    console.print(f"DB: {orch.store.path}")
    console.print(f"Universe: {len(orch.universe.all_tickers)} tickers "
                  f"({len(orch.universe.stocks)} stocks, "
                  f"{len(orch.universe.indices)} indices, "
                  f"{len(orch.universe.funds)} funds)")
    console.print(f"Base currency: {orch.universe.base_currency}")
    pf = orch.portfolio.value()
    console.print(f"Portfolio: {pf.total_value_chf:,.2f} CHF "
                  f"(starting {pf.starting_cash_chf:,.2f})")


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
