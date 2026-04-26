"""Persistent storage (SQLite) for prices, predictions, trades and model performance."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "investai.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL,
    adj_close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    made_on TEXT NOT NULL,           -- date prediction was made
    target_date TEXT NOT NULL,       -- date the prediction targets
    horizon_days INTEGER NOT NULL,
    expected_return REAL NOT NULL,   -- forecasted return over horizon
    direction_prob REAL NOT NULL,    -- probability of upward move
    model TEXT NOT NULL,
    features TEXT,                   -- JSON snapshot of feature values
    realized_return REAL,            -- filled in retroactively
    error REAL,                      -- realized - expected
    UNIQUE(ticker, made_on, target_date, model)
);
CREATE INDEX IF NOT EXISTS idx_pred_ticker ON predictions(ticker);
CREATE INDEX IF NOT EXISTS idx_pred_target ON predictions(target_date);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_chf REAL NOT NULL,
    starting_cash_chf REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    quantity REAL NOT NULL,
    avg_cost_chf REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CHF'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,              -- BUY | SELL
    quantity REAL NOT NULL,
    price_native REAL NOT NULL,
    fx_rate REAL NOT NULL,           -- native -> CHF
    price_chf REAL NOT NULL,
    fee_chf REAL NOT NULL,
    rationale TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);

CREATE TABLE IF NOT EXISTS portfolio_history (
    ts TEXT PRIMARY KEY,
    total_value_chf REAL NOT NULL,
    cash_chf REAL NOT NULL,
    holdings_value_chf REAL NOT NULL,
    pnl_chf REAL NOT NULL,
    pnl_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS model_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluated_at TEXT NOT NULL,
    model TEXT NOT NULL,
    ticker TEXT,
    horizon_days INTEGER NOT NULL,
    sample_size INTEGER NOT NULL,
    mae REAL,
    rmse REAL,
    hit_rate REAL,                  -- directional accuracy
    sharpe REAL,
    params TEXT                     -- JSON of best params
);

CREATE TABLE IF NOT EXISTS model_params (
    model TEXT PRIMARY KEY,
    params TEXT NOT NULL,           -- JSON
    score REAL NOT NULL,
    metric TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Store:
    """Thin SQLite wrapper. One Store per process is fine; uses short-lived cursors."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.cursor() as cur:
            cur.executescript(_SCHEMA)

    # ---- prices -----------------------------------------------------------
    def upsert_prices(self, ticker: str, rows: Iterable[dict]) -> int:
        sql = (
            "INSERT INTO prices(ticker,date,open,high,low,close,adj_close,volume) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,adj_close=excluded.adj_close,volume=excluded.volume"
        )
        n = 0
        with self.cursor() as cur:
            for r in rows:
                cur.execute(sql, (ticker, r["date"], r.get("open"), r.get("high"),
                                  r.get("low"), r.get("close"), r.get("adj_close"),
                                  r.get("volume")))
                n += 1
        return n

    def fetch_prices(self, ticker: str) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM prices WHERE ticker=? ORDER BY date", (ticker,))
            return cur.fetchall()

    # ---- predictions ------------------------------------------------------
    def insert_prediction(self, **kw) -> None:
        sql = (
            "INSERT OR REPLACE INTO predictions(ticker,made_on,target_date,horizon_days,"
            "expected_return,direction_prob,model,features) VALUES(?,?,?,?,?,?,?,?)"
        )
        with self.cursor() as cur:
            cur.execute(sql, (kw["ticker"], kw["made_on"], kw["target_date"],
                              kw["horizon_days"], kw["expected_return"],
                              kw["direction_prob"], kw["model"],
                              json.dumps(kw.get("features", {}), default=float)))

    def update_prediction_realized(self, pred_id: int, realized: float) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE predictions SET realized_return=?, "
                "error=? - expected_return WHERE id=?",
                (realized, realized, pred_id))

    def pending_predictions(self, on_or_before_date: str) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM predictions WHERE realized_return IS NULL "
                "AND target_date <= ? ORDER BY target_date", (on_or_before_date,))
            return cur.fetchall()

    def predictions_for_evaluation(self, model: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM predictions WHERE realized_return IS NOT NULL"
        args: list = []
        if model:
            sql += " AND model=?"; args.append(model)
        sql += " ORDER BY target_date DESC LIMIT 2000"
        with self.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    # ---- portfolio --------------------------------------------------------
    def get_portfolio(self) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM portfolio WHERE id=1")
            return cur.fetchone()

    def init_portfolio(self, starting_cash: float, ts: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO portfolio(id,cash_chf,starting_cash_chf,created_at,updated_at) "
                "VALUES(1,?,?,?,?)",
                (starting_cash, starting_cash, ts, ts))

    def update_cash(self, cash: float, ts: str) -> None:
        with self.cursor() as cur:
            cur.execute("UPDATE portfolio SET cash_chf=?, updated_at=? WHERE id=1",
                        (cash, ts))

    def upsert_position(self, ticker: str, qty: float, avg_cost: float,
                        currency: str = "CHF") -> None:
        with self.cursor() as cur:
            if qty <= 1e-9:
                cur.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO positions(ticker,quantity,avg_cost_chf,currency) "
                    "VALUES(?,?,?,?)", (ticker, qty, avg_cost, currency))

    def get_positions(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM positions ORDER BY ticker")
            return cur.fetchall()

    def insert_trade(self, **kw) -> None:
        sql = ("INSERT INTO trades(ts,ticker,side,quantity,price_native,fx_rate,"
               "price_chf,fee_chf,rationale) VALUES(?,?,?,?,?,?,?,?,?)")
        with self.cursor() as cur:
            cur.execute(sql, (kw["ts"], kw["ticker"], kw["side"], kw["quantity"],
                              kw["price_native"], kw["fx_rate"], kw["price_chf"],
                              kw["fee_chf"], kw.get("rationale", "")))

    def list_trades(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM trades ORDER BY ts")
            return cur.fetchall()

    def insert_portfolio_history(self, ts: str, total: float, cash: float,
                                 holdings: float, pnl: float, pnl_pct: float) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO portfolio_history(ts,total_value_chf,cash_chf,"
                "holdings_value_chf,pnl_chf,pnl_pct) VALUES(?,?,?,?,?,?)",
                (ts, total, cash, holdings, pnl, pnl_pct))

    def portfolio_history(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_history ORDER BY ts")
            return cur.fetchall()

    # ---- model perf -------------------------------------------------------
    def insert_model_perf(self, **kw) -> None:
        sql = ("INSERT INTO model_performance(evaluated_at,model,ticker,horizon_days,"
               "sample_size,mae,rmse,hit_rate,sharpe,params) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)")
        with self.cursor() as cur:
            cur.execute(sql, (kw["evaluated_at"], kw["model"], kw.get("ticker"),
                              kw["horizon_days"], kw["sample_size"], kw.get("mae"),
                              kw.get("rmse"), kw.get("hit_rate"), kw.get("sharpe"),
                              json.dumps(kw.get("params", {}))))

    def save_model_params(self, model: str, params: dict, score: float,
                          metric: str, ts: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO model_params(model,params,score,metric,updated_at) "
                "VALUES(?,?,?,?,?)",
                (model, json.dumps(params), score, metric, ts))

    def load_model_params(self, model: str) -> dict | None:
        with self.cursor() as cur:
            cur.execute("SELECT params FROM model_params WHERE model=?", (model,))
            row = cur.fetchone()
            return json.loads(row["params"]) if row else None
