from datetime import timedelta

from bull_brain.orb import ET, Bar
from bull_brain.tests.test_orb import D, OR, mk
from bull_brain.variants import VConfig, backtest_v, simulate_day

C = dict(slippage_bps=0, max_stop_pct=0.05, risk_fraction=0.01)


def test_breakout_matches_basic_long_target():
    t = simulate_day(mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0, 103.0]), VConfig(**C), 20_000)[0][0]
    assert t.side == "LONG" and t.reason == "target" and t.pnl > 0


def test_fade_shorts_upside_break_and_targets_midpoint():
    # break up then fall back to the range middle (~100.0)
    bars = mk(OR + [100.5, 100.4, 100.2, 100.0, 99.9, 99.9])
    t = simulate_day(bars, VConfig(mode="fade", **C), 20_000)[0][0]
    assert t.side == "SHORT" and t.reason == "target" and t.pnl > 0


def test_fade_stops_out_when_break_continues():
    bars = mk(OR + [100.5, 100.6, 101.2, 102.0, 103.0])
    t = simulate_day(bars, VConfig(mode="fade", stop_mult=2.0, **C), 20_000)[0][0]
    assert t.side == "SHORT" and t.reason == "stop" and t.pnl < 0


def test_fade_skips_when_price_already_past_stop():
    bars = mk(OR + [100.5, 100.6, 101.2, 102.0, 103.0])  # entry would be beyond a 0.5-range stop
    assert simulate_day(bars, VConfig(mode="fade", **C), 20_000)[0] == []


def test_vwap_filter_blocks_breakout_below_vwap():
    # heavy volume at high prices early drags VWAP above the breakout close
    bars = [Bar(b.ts, b.open, b.high, b.low, b.close, 1000 if i < 3 else 1) for i, b in enumerate(mk([105.0] * 3 + [100.0] * 12 + [100.5]))]
    assert simulate_day(bars, VConfig(filters=("vwap",), **C), 20_000)[0] == []


def test_exit_slippage_and_fees_reduce_pnl():
    bars = mk(OR + [100.5, 100.4, 99.5, 99.0, 99.0])  # stop-out
    a = simulate_day(bars, VConfig(**C), 20_000)[0][0]
    b = simulate_day(bars, VConfig(slippage_bps=3, fee_bps=5, max_stop_pct=0.05, risk_fraction=0.01), 20_000)[0][0]
    assert b.pnl / b.shares < a.pnl / a.shares  # worse per share once costs apply


def test_gap_filter_needs_previous_close():
    bars = mk(OR + [100.5, 100.6, 101.0])
    assert simulate_day(bars, VConfig(filters=("gap",), **C), 20_000)[1] == "no previous close for gap filter"
    # day 2 with gap up from day 1's close (99) -> longs allowed
    d1 = mk([99.0] * 30)
    d2 = mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0], start=D + timedelta(days=1))
    res = backtest_v(d1 + d2, VConfig(filters=("gap",), **C), 20_000)
    assert len(res.trades) == 1 and res.trades[0].side == "LONG"


def test_fractional_crypto_sizing():
    from bull_brain.variants import _size
    q = _size(VConfig(fractional=True, max_notional_fraction=1.0), 10_000, 60_000.0, 59_000.0)
    assert 0 < q < 1 and q != int(q)
