from datetime import timedelta

from bull_brain.gap import GapConfig, simulate_gap_day
from bull_brain.tests.test_orb import D, mk

PREV = 100.0
C = dict(slippage_bps=0, min_stop_pct=0.0001, max_stop_pct=0.1)


def test_gap_go_long_hits_target():
    # gap up 2% to 102, drifts up in the window, then runs
    bars = mk([102.0, 102.1, 102.2, 102.3, 102.4] + [102.5, 103.0, 104.0, 105.0, 106.0, 107.0])
    t = simulate_gap_day(bars, PREV, GapConfig(mode="go", min_gap_pct=0.01, **C))
    assert t and t.side == "LONG" and t.reason == "target" and t.r_multiple > 1.9


def test_gap_go_needs_confirmation():
    bars = mk([102.0, 101.9, 101.8, 101.7, 101.6] + [101.5] * 6)
    assert simulate_gap_day(bars, PREV, GapConfig(mode="go", **C)) is None


def test_gap_fade_targets_prior_close():
    bars = mk([102.0, 101.8, 101.6, 101.4, 101.2] + [101.0, 100.5, 100.0, 99.9, 99.9, 99.9])
    t = simulate_gap_day(bars, PREV, GapConfig(mode="fade", **C))
    assert t and t.side == "SHORT" and t.reason == "target" and t.exit == PREV


def test_small_or_giant_gap_ignored_and_no_prev_close():
    bars = mk([100.2, 100.3, 100.4, 100.5, 100.6] + [101.0] * 6)
    assert simulate_gap_day(bars, PREV, GapConfig(mode="go", **C)) is None
    big = mk([130.0, 130.1, 130.2, 130.3, 130.4] + [131.0] * 6)
    assert simulate_gap_day(big, PREV, GapConfig(mode="go", **C)) is None
    assert simulate_gap_day(bars, None, GapConfig()) is None
