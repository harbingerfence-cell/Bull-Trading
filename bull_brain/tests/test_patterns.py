from datetime import datetime, timedelta, timezone

import pytest

import bull_brain.patterns as P
from bull_brain.orb import Bar

D0 = datetime(2020, 1, 2, tzinfo=timezone.utc)


def path(points, pad=0):
    """Daily bars from (index, price) waypoints, linearly interpolated, +-0.3% ranges."""
    pts = [(i + pad, p) for i, p in points]
    closes = [pts[0][1]] * pad
    for (i0, p0), (i1, p1) in zip(pts, pts[1:]):
        for i in range(i0, i1):
            closes.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
    closes.append(pts[-1][1])
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(D0 + timedelta(days=i), o, max(o, c) * 1.003, min(o, c) * 0.997, c, 1e6))
        prev = c
    return out


def first_signal(name, bars):
    s = P.Series(bars)
    for t in range(P.K + 2, len(bars)):
        sig = P.DETECTORS[name](s, t)
        if sig:
            return t, sig
    return None, None


CASES = {
    "double_bottom": ([(0, 110), (20, 100), (35, 112), (50, 100.5), (60, 108), (68, 114)], "LONG"),
    "double_top": ([(0, 100), (20, 110), (35, 98), (50, 109.5), (60, 102), (68, 95)], "SHORT"),
    "head_shoulders": ([(0, 100), (20, 120), (30, 110), (45, 130), (58, 111), (70, 121), (80, 100)], "SHORT"),
    "inverse_head_shoulders": ([(0, 140), (20, 120), (30, 130), (45, 110), (58, 129), (70, 119), (80, 140)], "LONG"),
    "ascending_triangle": ([(0, 105), (10, 100), (20, 110), (30, 103), (40, 110), (50, 106), (60, 110),
                           (64, 108), (72, 114)], "LONG"),
    "descending_triangle": ([(0, 95), (10, 100), (20, 90), (30, 97), (40, 90), (50, 94), (60, 90),
                            (64, 92), (72, 86)], "SHORT"),
    "bull_flag": ([(0, 100), (30, 100), (38, 115), (49, 111.5), (52, 118)], "LONG"),
    "bear_flag": ([(0, 100), (30, 100), (38, 85), (49, 88.5), (52, 82)], "SHORT"),
    "cup_handle": ([(0, 100), (30, 100), (40, 120), (80, 95), (120, 118), (128, 114), (135, 118), (138, 121)], "LONG"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_pattern_detected_with_sane_levels(name):
    pts, side = CASES[name]
    bars = path(pts)
    t, sig = first_signal(name, bars)
    assert sig is not None, f"{name} not detected"
    assert sig.side == side and sig.pattern == name
    c = bars[t].close
    if side == "LONG":
        assert sig.stop < c < sig.target
    else:
        assert sig.target < c < sig.stop


def test_no_lookahead_pivots_and_truncation_invariance():
    bars = path(CASES["double_bottom"][0])
    s = P.Series(bars)
    for t in range(len(bars)):
        assert all(i <= t - P.K for i, _ in s.lows(t, 200)) and all(i <= t - P.K for i, _ in s.highs(t, 200))
    t, sig = first_signal("double_bottom", bars)
    trunc = P.Series(bars[:t + 1])
    assert P.double_bottom(trunc, t) == sig  # same answer with the future removed


def test_flat_market_produces_no_signals():
    bars = path([(0, 100), (200, 101)])
    s = P.Series(bars)
    assert all(d(s, t) is None for d in P.DETECTORS.values() for t in range(P.K + 2, len(bars)))


# --- trade simulation ------------------------------------------------------
def fake_detector(side, stop, target, at):
    return lambda s, t: P.Signal("fake", side, stop, target) if t == at else None


def flatbars(n=30, price=100.0):
    return [Bar(D0 + timedelta(days=i), price, price * 1.001, price * 0.999, price, 1e6) for i in range(n)]


def run(bars, sig, monkeypatch, **kw):
    monkeypatch.setitem(P.DETECTORS, "fake", fake_detector(*sig))
    return P.backtest_pattern("T", bars, "fake", slippage_bps=0, warmup=5, **kw)


def replace(bars, i, **kw):
    b = bars[i]
    d = dict(open=b.open, high=b.high, low=b.low, close=b.close)
    d.update(kw)
    bars[i] = Bar(b.ts, d["open"], d["high"], d["low"], d["close"], b.volume)


def test_target_stop_first_gap_and_time(monkeypatch):
    # target: next day rallies through 106
    b = flatbars(); replace(b, 11, high=106.5, close=106)
    tr = run(b, ("LONG", 98.0, 106.0, 10), monkeypatch)[0]
    assert tr.reason == "target" and tr.r == pytest.approx(3.0)
    # stop first when a bar spans both
    b = flatbars(); replace(b, 11, high=107, low=97)
    tr = run(b, ("LONG", 98.0, 106.0, 10), monkeypatch)[0]
    assert tr.reason == "stop" and tr.r == pytest.approx(-1.0)
    # gap through the stop after entry day exits at the (worse) open
    b = flatbars(); replace(b, 12, open=95, high=95.5, low=94, close=95)
    tr = run(b, ("LONG", 98.0, 106.0, 10), monkeypatch)[0]
    assert tr.reason == "stop_gap" and tr.r < -1.4
    # time exit
    tr = run(flatbars(60), ("LONG", 98.0, 106.0, 10), monkeypatch, max_hold=5)[0]
    assert tr.reason == "time" and tr.bars_held == 5 and abs(tr.r) < 0.2


def test_skips_bad_entries_and_short_side(monkeypatch):
    b = flatbars(); replace(b, 11, open=97, high=97.2, low=96.8, close=97)   # opens beyond stop
    assert run(b, ("LONG", 98.0, 106.0, 10), monkeypatch) == []
    assert run(flatbars(), ("LONG", 99.0, 100.5, 10), monkeypatch) == []     # reward < 1R
    b = flatbars(); replace(b, 11, low=93.5, close=94)
    tr = run(b, ("SHORT", 102.0, 94.0, 10), monkeypatch)[0]
    assert tr.side == "SHORT" and tr.reason == "target" and tr.r == pytest.approx(3.0)


def test_slippage_hurts(monkeypatch):
    monkeypatch.setitem(P.DETECTORS, "fake", fake_detector("LONG", 98.0, 106.0, 10))
    b = flatbars(); replace(b, 11, high=106.5, close=106)
    free = P.backtest_pattern("T", b, "fake", slippage_bps=0, warmup=5)[0].r
    costly = P.backtest_pattern("T", b, "fake", slippage_bps=10, warmup=5)[0].r
    assert costly < free
