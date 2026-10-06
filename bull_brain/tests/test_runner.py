from datetime import timedelta

from bull_brain.market_data import MarketDataAdapter, StaticQuoteProvider
from bull_brain.models import AccountState, GateResult, Decision, Quote
from bull_brain.orb import ET, ORBConfig
from bull_brain.runner import ORBRunner, run_loop
from bull_brain.tests.test_acceptance import full_limits
from bull_brain.tests.test_orb import D, OR, mk

CFG = ORBConfig(slippage_bps=0, max_stop_pct=0.05, risk_fraction=0.01, max_trades_per_day=3)


class FakeBroker:
    def __init__(self, daily_pnl=0.0, position=False, approve=True):
        self.submitted, self.flattened = [], []
        self.daily_pnl, self.position, self.approve = daily_pnl, position, approve

    def account_state(self, instrument=None):
        return AccountState(equity=25_000, daily_pnl=self.daily_pnl)

    def has_position(self, s):
        return self.position

    def flatten(self, s):
        self.flattened.append(s)

    def submit(self, plan, now=None):
        self.submitted.append(plan)
        d = Decision.PAPER_CANDIDATE if self.approve else Decision.NO_TRADE
        return GateResult(plan_id=plan.plan_id, decision=d, approved=self.approve, reasons=["x"])


def setup(path, broker=None, cfg=CFG, quote=True):
    bars = mk(path)
    at = {"t": None}

    def src(sym, start, end):
        return [b for b in bars if b.ts < end]

    def qp(now):
        p = StaticQuoteProvider()
        if quote:
            p.set("SPY", "ALPACA", Quote(bid=100.45, ask=100.55, timestamp=now, source="t"))
        return p
    broker = broker or FakeBroker()
    r = ORBRunner("SPY", cfg, src, MarketDataAdapter(StaticQuoteProvider(), full_limits()), broker)
    return r, broker, qp


def tick(r, qp, minute):
    now = D + timedelta(minutes=minute)
    r.adapter.provider = qp(now)
    return r.step(now)


def test_waits_during_opening_range_then_no_signal():
    r, b, qp = setup(OR + [100.0, 100.0])
    assert tick(r, qp, 10).action == "WAIT"
    assert tick(r, qp, 16).detail == "no signal"
    assert not b.submitted


def test_breakout_submits_long_plan_through_broker():
    r, b, qp = setup(OR + [100.5])
    res = tick(r, qp, 16)  # bar 15 (09:45) completed -> close 100.5 > or_hi
    assert res.action == "SIGNAL_SUBMITTED" and len(b.submitted) == 1
    p = b.submitted[0]
    assert p.side.value == "LONG" and p.stop < p.entry and p.quantity > 0
    assert p.opposing_thesis and p.invalidation and p.quote is not None


def test_same_bar_not_resubmitted_and_open_position_blocks():
    r, b, qp = setup(OR + [100.5, 100.6])
    tick(r, qp, 16)
    assert tick(r, qp, 16).action == "WAIT" and len(b.submitted) == 1
    b.position = True
    assert tick(r, qp, 17).detail == "position open"


def test_missing_quote_blocks_submission():
    r, b, qp = setup(OR + [100.5], quote=False)
    res = tick(r, qp, 16)
    assert res.action == "NO_DATA" and not b.submitted


def test_daily_profit_target_and_loss_limit_stop_day():
    cfg = ORBConfig(slippage_bps=0, max_stop_pct=0.05, daily_profit_target=100)
    r, b, qp = setup(OR + [100.5], FakeBroker(daily_pnl=150), cfg)
    assert tick(r, qp, 16).action == "DONE" and not b.submitted
    cfg2 = ORBConfig(slippage_bps=0, max_stop_pct=0.05, daily_loss_limit=100)
    r, b, qp = setup(OR + [100.5], FakeBroker(daily_pnl=-120), cfg2)
    assert tick(r, qp, 16).action == "DONE"


def test_flatten_at_flat_time_only_if_position():
    r, b, qp = setup(OR + [100.0] * 90, FakeBroker(position=True))
    assert tick(r, qp, 91).action == "FLATTENED" and b.flattened == ["SPY"]
    r2, b2, qp2 = setup(OR + [100.0] * 90)
    assert tick(r2, qp2, 91).action == "DONE" and not b2.flattened


def test_incomplete_opening_range_sits_out():
    bars = [x for i, x in enumerate(mk(OR + [100.5])) if i not in (2, 3)]
    r, b, qp = setup(OR)
    r.bars_source = lambda s, st, en: bars
    assert tick(r, qp, 16).action == "NO_DATA" and tick(r, qp, 17).action == "DONE"


def test_gate_rejection_reported_and_halt_ends_day():
    r, b, qp = setup(OR + [100.5], FakeBroker(approve=False))
    assert tick(r, qp, 16).action == "SIGNAL_REJECTED"


def test_run_loop_stops_when_done():
    r, b, qp = setup(OR + [100.0] * 90)
    clock = {"m": 91}
    out = []

    def now():
        t = D + timedelta(minutes=clock["m"])
        r.adapter.provider = qp(t)
        return t
    run_loop(r, now_fn=now, sleep_fn=lambda s: clock.update(m=clock["m"] + 1), on_result=lambda t, x: out.append(x.action))
    assert out[-1] in ("DONE", "FLATTENED")
