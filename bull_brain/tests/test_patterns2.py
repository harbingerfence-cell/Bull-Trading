from datetime import timedelta

import pytest

import bull_brain.patterns as P
from bull_brain.orb import Bar
from bull_brain.tests.test_patterns import D0, first_signal, path

# parabola for the rounding bottom: price = 90 + 0.0133*(i-30)^2 for i in 0..60, then a steady rise
ROUND = [(i, 90 + 0.0133 * (i - 30) ** 2) for i in range(0, 61, 3)] + [(66, 105.0), (70, 109.0)]

CASES2 = {
    "triple_bottom": ([(0, 110), (15, 100), (25, 108), (40, 100.5), (50, 108.5), (65, 100.2), (75, 113)], "LONG"),
    "triple_top": ([(0, 90), (15, 100), (25, 92), (40, 99.5), (50, 91.5), (65, 99.8), (75, 88)], "SHORT"),
    "falling_wedge": ([(0, 105), (10, 120), (20, 100), (30, 115), (40, 98), (50, 110), (60, 96), (66, 108)], "LONG"),
    "rising_wedge": ([(0, 95), (10, 100), (20, 90), (30, 102), (40, 95), (50, 104), (60, 100), (65, 102), (69, 96)], "SHORT"),
    "symmetrical_triangle_up": ([(0, 105), (10, 120), (20, 95), (30, 115), (40, 97), (50, 110), (60, 99), (66, 108)], "LONG"),
    "symmetrical_triangle_down": ([(0, 105), (10, 120), (20, 95), (30, 115), (40, 97), (50, 110), (60, 99), (64, 103), (69, 97)], "SHORT"),
    "rectangle_up": ([(0, 105), (10, 110), (20, 100), (30, 110), (40, 100), (50, 110), (60, 100), (70, 111.5)], "LONG"),
    "rectangle_down": ([(0, 105), (10, 100), (20, 110), (30, 100), (40, 110), (50, 100), (60, 110), (66, 99)], "SHORT"),
    "bull_pennant": ([(0, 100), (30, 100), (38, 115), (41, 111), (44, 114), (46, 112.3), (48, 113.3), (50, 112.6), (52, 118)], "LONG"),
    "bear_pennant": ([(0, 100), (30, 100), (38, 85), (41, 89), (44, 86), (46, 87.7), (48, 86.7), (50, 87.4), (52, 82)], "SHORT"),
    "rounding_bottom": (ROUND, "LONG"),
}


@pytest.mark.parametrize("name", list(CASES2))
def test_chart_pattern_detected(name):
    pts, side = CASES2[name]
    bars = path(pts, pad=0)
    t, sig = first_signal(name, bars)
    assert sig is not None, f"{name} not detected"
    assert sig.side == side and sig.pattern == name
    c = bars[t].close
    assert (sig.stop < c < sig.target) if side == "LONG" else (sig.target < c < sig.stop)


def candles(prices, extra):
    """prices: closes for a drift segment (1% daily ranges); extra: explicit (o,h,l,c) pattern bars."""
    out, prev = [], prices[0]
    for i, c in enumerate(prices):
        o = prev
        out.append(Bar(D0 + timedelta(days=i), o, max(o, c) * 1.002, min(o, c) * 0.998, c, 1e6))
        prev = c
    for j, (o, h, l, c) in enumerate(extra):
        out.append(Bar(D0 + timedelta(days=len(prices) + j), o, h, l, c, 1e6))
    return out


DOWN = [100 - 0.7 * i for i in range(11)]            # ends ~93, a >3% drop over the context window
UP = [100 + 0.7 * i for i in range(11)]              # ends ~107

CANDLE = {
    "bullish_engulfing": (DOWN, [(93.2, 93.3, 92.2, 92.4), (92.3, 93.9, 92.2, 93.8)], "LONG"),
    "bearish_engulfing": (UP, [(106.8, 107.8, 106.7, 107.7), (107.8, 107.9, 106.2, 106.4)], "SHORT"),
    "hammer": (DOWN, [(92.8, 93.0, 90.8, 92.9)], "LONG"),
    "shooting_star": (UP, [(107.2, 109.4, 107.1, 107.3)], "SHORT"),
    "morning_star": (DOWN, [(93.0, 93.1, 90.0, 90.2), (89.6, 90.0, 89.3, 89.7), (90.3, 93.4, 90.2, 93.2)], "LONG"),
    "evening_star": (UP, [(107.0, 110.0, 106.9, 109.8), (110.4, 110.8, 110.1, 110.3), (109.6, 109.9, 106.7, 106.9)], "SHORT"),
}


@pytest.mark.parametrize("name", list(CANDLE))
def test_candlestick_detected_only_in_context(name):
    ctx, extra, side = CANDLE[name]
    bars = candles(ctx, extra)
    s = P.Series(bars)
    sig = P.DETECTORS[name](s, len(bars) - 1)
    assert sig is not None and sig.side == side
    c = bars[-1].close
    assert (sig.stop < c < sig.target) if side == "LONG" else (sig.target < c < sig.stop)
    flat = candles([100.0] * 11, extra)               # same candles with no preceding trend -> no signal
    assert P.DETECTORS[name](P.Series(flat), len(flat) - 1) is None


def test_hold_days_and_registry():
    assert len(P.DETECTORS) == 32 and P.HOLD_DAYS["hammer"] == 10
    bars = candles(DOWN, CANDLE["hammer"][1] + [(92.9, 93.2, 92.8, 93.1)] * 15)
    # 10-day default for candlesticks is honoured by backtest_pattern via HOLD_DAYS
    tr = P.backtest_pattern("T", bars, "hammer", warmup=9, slippage_bps=0)
    assert tr and tr[0].bars_held <= 10


def mirror(points, top):
    """Mirror waypoints around `top` so a bullish setup becomes its bearish twin."""
    return [(i, top - p) for i, p in points]


CUP = CASES["cup_handle"][0] if False else [(0, 100), (30, 100), (40, 120), (80, 95), (120, 118), (128, 114), (135, 118), (138, 121)]
MEGA = [(0, 100), (10, 104), (20, 96), (30, 108), (40, 92), (50, 112), (60, 88), (66, 103), (70, 118)]
MEGA_DN = [(0, 100), (10, 96), (20, 104), (30, 92), (40, 108), (50, 88), (60, 112), (66, 98), (72, 80)]


def test_inverse_cup_handle_and_rounding_top_and_megaphones():
    t, sig = first_signal("inverse_cup_handle", path(mirror(CUP, 220)))
    assert sig and sig.side == "SHORT"
    pts = [(i, 130 - 0.02 * (i - 35.5) ** 2) for i in list(range(0, 63, 3)) + [65]] + [(68, 108.0), (72, 100.0)]
    t, sig = first_signal("rounding_top", path(pts))
    assert sig and sig.side == "SHORT"
    t, sig = first_signal("megaphone_up", path(MEGA))
    assert sig and sig.side == "LONG" and sig.stop < path(MEGA)[t].close < sig.target
    t, sig = first_signal("megaphone_down", path(MEGA_DN))
    assert sig and sig.side == "SHORT"


def test_island_reversals_need_both_gaps_and_context():
    down = candles(DOWN, [(91.0, 91.4, 90.6, 90.8),                        # gap down island bar (high < prior low)
                          (90.7, 91.0, 90.4, 90.6),
                          (92.0, 93.2, 91.9, 93.0)])                       # gap up above the island highs
    s = P.Series(down)
    sig = P.island_reversal_bullish(s, len(down) - 1)
    assert sig and sig.side == "LONG" and sig.stop < down[-1].close < sig.target
    nogap = candles(DOWN, [(91.0, 91.4, 90.6, 90.8), (90.7, 91.0, 90.4, 90.6), (90.8, 92.0, 90.7, 91.8)])
    assert P.island_reversal_bullish(P.Series(nogap), len(nogap) - 1) is None
    up = candles(UP, [(110.0, 110.4, 109.6, 110.2), (110.3, 110.6, 110.0, 110.1), (108.6, 108.9, 107.5, 107.7)])
    sig = P.island_reversal_bearish(P.Series(up), len(up) - 1)
    assert sig and sig.side == "SHORT"
    assert len(P.DETECTORS) == 32
