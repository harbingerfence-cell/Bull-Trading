import json
from datetime import timedelta

import pytest

from bull_brain.market_data import MarketDataAdapter, StaticQuoteProvider
from bull_brain.models import Quote
from bull_brain.paper_broker import PaperBroker
from bull_brain.tests.test_acceptance import NOW, full_limits
from bull_brain.webhook import AlertHandler

SECRET = "s" * 24


def setup(limits=None, quote=True, **kw):
    limits = limits or full_limits()
    prov = StaticQuoteProvider()
    if quote:
        prov.set("XYZ", "ALPACA", Quote(bid=99.95, ask=100.05, timestamp=NOW - timedelta(seconds=2), source="t"))
    broker = PaperBroker(limits, 10_000)
    return AlertHandler(broker, MarketDataAdapter(prov, limits), limits, SECRET, {"XYZ"}, **kw), broker


def alert(**o):
    base = dict(secret=SECRET, alert_id="a1", symbol="XYZ", side="LONG", stop_pct=0.02,
                time=NOW.isoformat(), thesis="trend", opposing_thesis="failed breakout", invalidation="below stop")
    base.update(o)
    return json.dumps(base).encode()


def test_valid_alert_sized_gated_and_filled():
    h, b = setup()
    code, out = h.handle(alert(), NOW)
    assert code == 200 and out["status"] == "submitted" and out["plan_id"] == "tv-a1"
    # risk 1% of 10k = 100; stop 2% of 100.05 ~ 2.0 -> ~49 shares, gate max is floor(100/2.001)=49
    assert 40 <= out["quantity"] <= 50 and "tv-a1" in b.positions


def test_bad_secret_unauthorized_and_no_order():
    h, b = setup()
    assert h.handle(alert(secret="x" * 24), NOW)[0] == 401 and not b.positions


def test_duplicate_alert_ignored():
    h, b = setup()
    h.handle(alert(), NOW)
    assert h.handle(alert(), NOW)[1]["status"] == "duplicate" and len(b.positions) == 1


def test_stale_alert_rejected_and_unknown_symbol():
    h, b = setup()
    old = (NOW - timedelta(minutes=10)).isoformat()
    assert h.handle(alert(time=old), NOW)[1]["status"] == "rejected"
    assert h.handle(alert(alert_id="a2", symbol="ZZZ"), NOW)[1]["status"] == "rejected"
    assert not b.positions


def test_missing_reasoning_blocked_by_gate_not_defaulted():
    h, b = setup()
    code, out = h.handle(alert(opposing_thesis="", invalidation=""), NOW)
    assert out["status"] == "blocked" and out["decision"] == "NO_TRADE" and not b.positions


def test_no_quote_means_no_data_and_alert_price_never_trusted():
    h, b = setup(quote=False)
    out = h.handle(alert(price=1.0), NOW)[1]
    assert out["status"] == "no_data" and not b.positions


def test_validation_errors():
    h, _ = setup()
    assert h.handle(b"not json", NOW)[0] == 400
    assert h.handle(alert(stop=None, stop_pct=None), NOW)[0] == 400                 # neither stop
    assert h.handle(alert(alert_id="b", stop=99.0, stop_pct=0.01), NOW)[0] == 400   # both
    assert h.handle(alert(alert_id="c", symbol="X;DROP"), NOW)[0] == 400
    assert h.handle(alert(alert_id="d", stop=101.0), NOW)[0] == 400                 # long stop above price
    assert h.handle(b"{" + b" " * 9000 + b"}", NOW)[0] == 413


def test_daily_cap_and_short_secret_refused():
    h, _ = setup(max_alerts_per_day=2)
    for i in range(2):
        h.handle(alert(alert_id=f"k{i}"), NOW)
    assert h.handle(alert(alert_id="k9"), NOW)[1]["reasons"] == ["daily alert cap reached"]
    with pytest.raises(ValueError):
        AlertHandler(None, None, full_limits(), "short", {"XYZ"})


def test_prefixed_symbol_and_short_side():
    h, b = setup()
    code, out = h.handle(alert(alert_id="s1", symbol="NASDAQ:XYZ", side="SHORT"), NOW)
    assert out["status"] == "submitted" and b.positions["tv-s1"].plan.side.value == "SHORT"
