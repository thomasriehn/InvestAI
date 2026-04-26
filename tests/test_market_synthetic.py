from __future__ import annotations

from investai.data.market import MarketData
from investai.db.store import Store


def test_synthetic_history_caches(tmp_db):
    store = Store(tmp_db)
    md = MarketData(store, allow_synthetic=True)
    df = md.history("FAKE.X", lookback_days=300, refresh=True)
    assert not df.empty
    assert len(df) > 100
    # second call uses cache (no refresh)
    df2 = md.history("FAKE.X", refresh=False)
    assert len(df2) == len(df)


def test_quote_returns_currency(tmp_db):
    store = Store(tmp_db)
    md = MarketData(store, allow_synthetic=True)
    md.history("AAPL", lookback_days=200, refresh=True)
    q = md.quote("AAPL")
    assert q is not None
    assert q.price > 0
    assert q.currency in {"USD", "CHF", "EUR", "GBP"}
