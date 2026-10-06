import pytest

from bull_brain.alpaca_broker import AlpacaPaperBroker, UncertainOrder, _urllib_transport
from bull_brain.alpaca_data import AlpacaError
from bull_brain.models import Decision, Mode
from bull_brain.tests.test_acceptance import NOW, full_limits, plan


class Fake:
    def __init__(self, post=None, existing=None):
        self.calls, self.post, self.existing = [], post, existing

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("alpaca.markets")[1]
        self.calls.append((method, path, body))
        if path == "/v2/account":
            return 200, {"equity": "10000", "last_equity": "10000"}
        if path == "/v2/positions" and method == "GET":
            return 200, []
        if path.startswith("/v2/orders:by_client_order_id"):
            return (200, self.existing) if self.existing else (404, {})
        if path == "/v2/orders" and method == "POST":
            if isinstance(self.post, Exception):
                raise self.post
            return self.post or (200, {"id": "abc", "status": "accepted"})
        return 200, {}


def broker(fake, **kw):
    return AlpacaPaperBroker(full_limits(**kw), key_id="k", secret="s", transport=fake)


def posts(f):
    return [c for c in f.calls if c[0] == "POST"]


def test_approved_plan_places_bracket_order_with_client_id():
    f = Fake()
    r = broker(f).submit(plan(), NOW)
    assert r.approved
    body = posts(f)[0][2]
    assert body["order_class"] == "bracket" and body["client_order_id"] == "t1"
    assert body["qty"] == "40" and body["side"] == "buy"
    assert body["stop_loss"]["stop_price"] == "98.00" and body["take_profit"]["limit_price"] == "104.00"


def test_gate_failure_places_nothing():
    f = Fake()
    r = broker(f).submit(plan(opposing_thesis=""), NOW)
    assert r.decision is Decision.NO_TRADE and not posts(f)


def test_non_paper_and_non_equity_rejected():
    f = Fake()
    b = broker(f)
    assert not b.submit(plan(mode=Mode.LIVE), NOW).approved
    assert not b.submit(plan(plan_id="t2", multiplier=100), NOW).approved
    assert not posts(f)


def test_duplicate_detected_at_broker():
    f = Fake(existing={"id": "old", "status": "filled"})
    r = broker(f).submit(plan(), NOW)
    assert not r.approved and "duplicate" in r.reasons[0] and not posts(f)


def test_uncertain_post_reconciles_instead_of_retrying():
    class F2(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if method == "POST":
                self.calls.append((method, "/v2/orders", body))
                self.existing = {"id": "late", "status": "accepted"}
                raise UncertainOrder("timeout")
            if "by_client_order_id" in url and not any(c[0] == "POST" for c in self.calls):
                self.calls.append((method, "lookup", None))
                return 404, {}
            return super().__call__(method, url, headers, body, timeout)
    f = F2()
    b = broker(f)
    r = b.submit(plan(), NOW)
    assert r.approved and len(posts(f)) == 1  # exactly one POST, found via lookup
    assert b.journal.entries("t1", "fill")[0]["data"]["order_id"] == "late"


def test_uncertain_post_with_no_order_raises_and_journals():
    f = Fake(post=UncertainOrder("timeout"))
    b = broker(f)
    with pytest.raises(UncertainOrder):
        b.submit(plan(), NOW)
    assert len(posts(f)) == 1 and b.journal.entries("t1", "reject")


def test_http_rejection_raises():
    with pytest.raises(AlpacaError):
        broker(Fake(post=(403, {"message": "forbidden"}))).submit(plan(), NOW)


def test_account_loss_halts(monkeypatch):
    f = Fake()
    orig = f.__call__
    f.__class__.__call__ = lambda self, m, u, h, b, t: (200, {"equity": "9400", "last_equity": "10000"}) \
        if u.endswith("/v2/account") else orig(m, u, h, b, t)
    r = broker(f, daily_loss_limit=500).submit(plan(), NOW)
    assert r.decision is Decision.HALT


def test_paper_host_pinned_and_no_secrets_in_repr():
    with pytest.raises(AlpacaError):
        _urllib_transport("GET", "https://api.alpaca.markets/v2/account", {}, None, 1)
    assert "s" != repr(broker(Fake())) and "secret" not in repr(broker(Fake()))
