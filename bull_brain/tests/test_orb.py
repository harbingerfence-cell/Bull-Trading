from datetime import datetime, timedelta

from bull_brain.orb import ET, Bar, ORBConfig, backtest, run_day

D = datetime(2026, 10, 6, 9, 30, tzinfo=ET)


def mk(closes, start=D, spread=0.05):
    """1-min bars; each bar opens at the prior close."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(start + timedelta(minutes=i), o, max(o, c) + spread, min(o, c) - spread, c))
        prev = c
    return out


# 15 flat-ish OR bars around 100 (range ~ 99.9-100.1+spread), then scenario
OR = [100.0, 100.1, 99.9] * 5
CFG = ORBConfig(slippage_bps=0, max_stop_pct=0.05)


def test_long_hits_target():
    bars = mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0, 103.0])
    trades, skip = run_day(bars, CFG, 20_000)
    assert skip is None and trades[0].side == "LONG" and trades[0].reason == "target"
    assert trades[0].pnl > 0 and trades[0].r_multiple > 1.4


def test_entry_is_next_bar_open_not_signal_close():
    bars = mk(OR + [100.5, 100.8, 101.0])
    t = run_day(bars, CFG, 20_000)[0][0]
    assert t.entry == bars[16].open  # signal on bar 15 close, fill on bar 16 open


def test_stop_out_loses_about_one_r():
    bars = mk(OR + [100.5, 100.4, 99.5, 99.0, 99.0])
    t = run_day(bars, CFG, 20_000)[0][0]
    assert t.reason == "stop" and -1.2 < t.r_multiple < -0.8


def test_stop_assumed_first_when_both_in_one_bar():
    bars = mk(OR + [100.5, 100.4])
    bars.append(Bar(D + timedelta(minutes=17), 100.4, 110.0, 90.0, 100.4))
    t = run_day(bars, CFG, 20_000)[0][0]
    assert t.reason == "stop"


def test_incomplete_opening_range_skipped():
    bars = [b for i, b in enumerate(mk(OR + [101] * 10)) if i not in (3, 4, 5)]  # data gap in range
    trades, skip = run_day(bars, CFG, 20_000)
    assert trades == [] and skip == "incomplete opening range"


def test_daily_profit_target_stops_trading():
    cfg = ORBConfig(slippage_bps=0, max_stop_pct=0.05, daily_profit_target=1.0)
    bars = mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0, 99.0, 98.0, 97.0, 96.0])
    trades, _ = run_day(bars, cfg, 20_000)
    assert len(trades) == 1 and trades[0].pnl > 0


def test_max_trades_cap_and_rearm():
    cfg = ORBConfig(slippage_bps=0, max_stop_pct=0.05, max_trades_per_day=2, target_r=10)
    # long stop-out, re-arm inside range, long again stops, third blocked by cap
    path = OR + [100.5, 100.4, 99.5, 100.0, 100.5, 100.4, 99.5, 100.0, 100.5, 100.4, 99.5]
    trades, _ = run_day(mk(path), cfg, 20_000)
    assert len(trades) <= 2


def test_flat_at_time_stop():
    bars = mk(OR + [100.5, 100.6] + [100.6] * 80)
    t = run_day(bars, ORBConfig(slippage_bps=0, max_stop_pct=0.05, target_r=50), 20_000)[0][0]
    assert t.reason == "time" and t.exit_time.astimezone(ET).hour == 11


def test_short_side_and_summary():
    bars = mk(OR + [99.5, 99.4, 99.0, 98.0, 97.0, 96.5, 96.5])
    res = backtest(bars, CFG, 20_000)
    t = res.trades[0]
    assert t.side == "SHORT" and t.pnl > 0
    s = res.summary()
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["total_pnl"] > 0


def test_costs_reduce_pnl():
    bars = mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0, 103.0])
    free = run_day(bars, CFG, 20_000)[0][0].pnl
    costly = run_day(bars, ORBConfig(slippage_bps=2, cost_per_share=0.01, max_stop_pct=0.05), 20_000)[0][0].pnl
    assert costly < free
