from datetime import timedelta

from bull_brain.market_data import MarketDataAdapter, QuoteStatus, StaticQuoteProvider
from bull_brain.models import Decision, RiskLimits
from bull_brain.paper_broker import PaperBroker
from bull_brain.risk_gate import evaluate
from bull_brain.models import AccountState
from bull_brain.tests.test_acceptance import NOW, full_limits, plan, quote


def adapter(q=None, limits=None):
    prov = StaticQuoteProvider({("XYZ", "TEST"): q} if q else {})
    return MarketDataAdapter(prov, limits or full_limits())


def test_fresh_quote_attached_and_approved():
    a = adapter(quote(age_s=2))
    p, chk = a.attach(plan(quote=None), NOW)
    assert chk.status is QuoteStatus.FRESH and chk.usable and p.quote is not None
    assert PaperBroker(full_limits(), 10_000).submit(p, NOW).approved


def test_original_plan_not_mutated():
    a = adapter(quote())
    orig = plan(quote=None)
    a.attach(orig, NOW)
    assert orig.quote is None


def test_missing_quote_clears_stale_caller_quote_and_needs_data():
    a = adapter()  # provider has nothing
    p, chk = a.attach(plan(quote=quote(age_s=1)), NOW)
    assert chk.status is QuoteStatus.UNAVAILABLE and p.quote is None
    r = evaluate(p, full_limits(), AccountState(equity=10_000), NOW)
    assert r.decision is Decision.NEEDS_DATA and "quote" in r.missing


def test_stale_quote_flagged_and_gate_blocks():
    a = adapter(quote(age_s=60))
    p, chk = a.attach(plan(quote=None), NOW)
    assert chk.status is QuoteStatus.STALE and not chk.usable
    r = evaluate(p, full_limits(), AccountState(equity=10_000), NOW)
    assert r.decision is Decision.NEEDS_DATA and "stale" in " ".join(r.reasons)


def test_delayed_and_future_quotes():
    assert adapter(quote(delayed=True)).fetch("XYZ", "TEST", NOW)[1].status is QuoteStatus.DELAYED
    fut = quote(age_s=-30)
    assert adapter(fut).fetch("XYZ", "TEST", NOW)[1].status is QuoteStatus.FUTURE


def test_unset_age_limit_is_unchecked_not_fresh():
    a = adapter(quote(), RiskLimits())
    chk = a.fetch("XYZ", "TEST", NOW)[1]
    assert chk.status is QuoteStatus.UNCHECKED and not chk.usable


def test_provider_exception_returns_error_and_no_quote():
    class Boom:
        def get_quote(self, i, v):
            raise TimeoutError("feed down")
    p, chk = MarketDataAdapter(Boom(), full_limits()).attach(plan(), NOW)
    assert chk.status is QuoteStatus.ERROR and "feed down" in chk.detail and p.quote is None
