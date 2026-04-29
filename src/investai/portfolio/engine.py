"""Demo portfolio engine.

Maintains a 10'000 CHF demo portfolio and turns forecasts into BUY/SELL trades
under simple risk rules:

* hard cap on per-position weight,
* keep a small cash reserve,
* charge a realistic Swiss broker fee,
* refuse trades smaller than ``min_trade_chf``,
* always quote and record fees in CHF.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from ..data.market import MarketData
from ..db.store import Store
from ..forecast.engine import Forecast
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class Snapshot:
    timestamp: str
    cash_chf: float
    holdings_value_chf: float
    total_value_chf: float
    starting_cash_chf: float
    pnl_chf: float
    pnl_pct: float
    positions: list[dict]


class PortfolioEngine:
    def __init__(self, store: Store, market: MarketData,
                 fx_pairs: dict[str, str], cfg: dict) -> None:
        self.store = store
        self.market = market
        self.fx_pairs = fx_pairs
        self.cfg = cfg
        self._ensure_initialised()

    # ------------------------------------------------------------------
    def _ensure_initialised(self) -> None:
        if self.store.get_portfolio() is None:
            ts = datetime.now(timezone.utc).isoformat()
            self.store.init_portfolio(float(self.cfg["starting_cash_chf"]), ts)
            log.info("Initialised demo portfolio with %.2f CHF",
                     self.cfg["starting_cash_chf"])

    # ------------------------------------------------------------------
    def _fee(self, gross_chf: float) -> float:
        return max(float(self.cfg.get("trading_fee_min_chf", 1.0)),
                   gross_chf * float(self.cfg.get("trading_fee_pct", 0.001)))

    def _price_in_chf(self, ticker: str) -> tuple[float, float, str] | None:
        q = self.market.quote(ticker)
        if q is None:
            return None
        fx = self.market.fx_to_chf(q.currency, self.fx_pairs)
        return q.price, fx, q.currency

    # ------------------------------------------------------------------
    def value(self) -> Snapshot:
        portfolio = self.store.get_portfolio()
        cash = float(portfolio["cash_chf"])
        starting = float(portfolio["starting_cash_chf"])
        positions = []
        holdings_chf = 0.0
        for pos in self.store.get_positions():
            ticker = pos["ticker"]
            qty = float(pos["quantity"])
            res = self._price_in_chf(ticker)
            if res is None:
                continue
            price, fx, currency = res
            mv = qty * price * fx
            holdings_chf += mv
            cost = qty * float(pos["avg_cost_chf"])
            positions.append({
                "ticker": ticker,
                "quantity": qty,
                "currency": currency,
                "price_native": price,
                "fx_rate": fx,
                "market_value_chf": mv,
                "avg_cost_chf": float(pos["avg_cost_chf"]),
                "cost_chf": cost,
                "unrealized_pnl_chf": mv - cost,
                "unrealized_pnl_pct": (mv / cost - 1.0) if cost > 0 else 0.0,
            })
        total = cash + holdings_chf
        pnl = total - starting
        pnl_pct = pnl / starting if starting > 0 else 0.0
        return Snapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            cash_chf=cash, holdings_value_chf=holdings_chf,
            total_value_chf=total, starting_cash_chf=starting,
            pnl_chf=pnl, pnl_pct=pnl_pct, positions=positions,
        )

    def record_history(self) -> Snapshot:
        snap = self.value()
        self.store.insert_portfolio_history(
            ts=snap.timestamp, total=snap.total_value_chf, cash=snap.cash_chf,
            holdings=snap.holdings_value_chf, pnl=snap.pnl_chf,
            pnl_pct=snap.pnl_pct,
        )
        return snap

    # ------------------------------------------------------------------
    def _execute(self, ticker: str, side: str, qty: float, price: float, fx: float,
                 currency: str, rationale: str) -> dict:
        gross_chf = qty * price * fx
        fee = self._fee(gross_chf)
        ts = datetime.now(timezone.utc).isoformat()
        portfolio = self.store.get_portfolio()
        cash = float(portfolio["cash_chf"])
        positions = {p["ticker"]: dict(p) for p in self.store.get_positions()}
        existing = positions.get(ticker)

        if side == "BUY":
            total_cost = gross_chf + fee
            if total_cost > cash:
                return {"status": "rejected", "reason": "insufficient_cash"}
            cash -= total_cost
            old_qty = float(existing["quantity"]) if existing else 0.0
            old_avg = float(existing["avg_cost_chf"]) if existing else 0.0
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + total_cost) / new_qty if new_qty > 0 else 0.0
            self.store.upsert_position(ticker, new_qty, new_avg, currency)
        elif side == "SELL":
            if not existing:
                return {"status": "rejected", "reason": "no_position"}
            old_qty = float(existing["quantity"])
            qty = min(qty, old_qty)
            if qty <= 1e-9:
                return {"status": "rejected", "reason": "zero_qty"}
            proceeds = gross_chf - fee
            cash += proceeds
            new_qty = old_qty - qty
            self.store.upsert_position(ticker, new_qty,
                                       float(existing["avg_cost_chf"]), currency)
        else:
            return {"status": "rejected", "reason": f"unknown_side:{side}"}

        self.store.update_cash(cash, ts)
        self.store.insert_trade(
            ts=ts, ticker=ticker, side=side, quantity=qty,
            price_native=price, fx_rate=fx, price_chf=price * fx,
            fee_chf=fee, rationale=rationale,
        )
        log.info("%s %s qty=%.4f @ %.2f %s (CHF %.2f, fee %.2f) — %s",
                 side, ticker, qty, price, currency, gross_chf, fee, rationale)
        return {"status": "executed", "qty": qty, "gross_chf": gross_chf, "fee_chf": fee}

    # ------------------------------------------------------------------
    def apply_forecasts(self, forecasts: Iterable[Forecast],
                        forecast_cfg: dict) -> list[dict]:
        buy_th = float(forecast_cfg["buy_threshold"])
        sell_th = float(forecast_cfg["sell_threshold"])
        conf_floor = float(forecast_cfg["confidence_floor"])
        max_pos_pct = float(self.cfg["max_position_pct"])
        cash_reserve_pct = float(self.cfg["cash_reserve_pct"])
        min_trade_chf = float(self.cfg["min_trade_chf"])

        results: list[dict] = []

        # Rank: highest expected return first; sells driven by lowest.
        ranked = sorted(forecasts, key=lambda f: f.expected_return, reverse=True)
        for fc in ranked:
            res = self._price_in_chf(fc.ticker)
            if res is None:
                continue
            price, fx, currency = res

            # Re-snapshot every iteration so caps reflect prior trades in the
            # same call (otherwise repeated buys in one batch could breach
            # ``max_position_pct``).
            snap = self.value()
            total = snap.total_value_chf
            held = {p["ticker"]: p for p in snap.positions}
            pos = held.get(fc.ticker)

            # SELL
            if fc.expected_return <= sell_th and (1 - fc.direction_prob) >= conf_floor and pos:
                qty = float(pos["quantity"])
                rationale = (f"forecast {fc.expected_return:+.2%}, "
                             f"down_prob {1 - fc.direction_prob:.0%}")
                results.append({"ticker": fc.ticker, "action": "SELL",
                                **self._execute(fc.ticker, "SELL", qty, price, fx,
                                                currency, rationale)})
                continue

            # BUY
            if fc.expected_return >= buy_th and fc.direction_prob >= conf_floor:
                cash_now = snap.cash_chf
                cash_floor = total * cash_reserve_pct
                spendable = cash_now - cash_floor
                if spendable < min_trade_chf:
                    continue
                cap_chf = max_pos_pct * total
                current_value = pos["market_value_chf"] if pos else 0.0
                room = max(0.0, cap_chf - current_value)
                budget = min(spendable, room,
                             max(min_trade_chf, total * 0.05))  # 5% sizing per signal
                if budget < min_trade_chf:
                    continue
                qty = budget / (price * fx)
                rationale = (f"forecast {fc.expected_return:+.2%}, "
                             f"up_prob {fc.direction_prob:.0%}")
                results.append({"ticker": fc.ticker, "action": "BUY",
                                **self._execute(fc.ticker, "BUY", qty, price, fx,
                                                currency, rationale)})
        return results

    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self.store.cursor() as cur:
            cur.execute("DELETE FROM portfolio")
            cur.execute("DELETE FROM positions")
            cur.execute("DELETE FROM trades")
            cur.execute("DELETE FROM portfolio_history")
        self._ensure_initialised()
