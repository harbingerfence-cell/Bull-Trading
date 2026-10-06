"""Classic chart-pattern detection on daily bars, with a frozen-rule backtester.

No lookahead: a swing pivot at bar i (k bars each side) is only usable from
bar i+k. A signal fires on the CLOSE of the bar that first breaks the pattern's
trigger level; the trade enters at the NEXT bar's open. Stop = far side of the
pattern, target = measured move. Parameters below are the textbook defaults and
are NOT to be tuned on the data they are judged on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Sequence

from .orb import Bar

K = 4  # pivot half-width (days)


@dataclass(frozen=True)
class Signal:
    pattern: str
    side: str            # LONG | SHORT
    stop: float
    target: float


class Series:
    """Bars + confirmed swing pivots (usable only after their confirming bars)."""

    def __init__(self, bars: Sequence[Bar], k: int = K):
        self.b, self.k = list(bars), k
        n = len(self.b)
        hi = [x.high for x in self.b]
        lo = [x.low for x in self.b]
        self.hi_piv: list[int] = []
        self.lo_piv: list[int] = []
        for i in range(k, n - k):
            if hi[i] > max(hi[i - k:i]) and hi[i] >= max(hi[i + 1:i + k + 1]):
                self.hi_piv.append(i)
            if lo[i] < min(lo[i - k:i]) and lo[i] <= min(lo[i + 1:i + k + 1]):
                self.lo_piv.append(i)

    def highs(self, t: int, lookback: int) -> list[tuple[int, float]]:
        return [(i, self.b[i].high) for i in self.hi_piv if t - lookback <= i <= t - self.k]

    def lows(self, t: int, lookback: int) -> list[tuple[int, float]]:
        return [(i, self.b[i].low) for i in self.lo_piv if t - lookback <= i <= t - self.k]

    def maxh(self, a: int, z: int) -> float:  # inclusive range
        return max(x.high for x in self.b[a:z + 1])

    def minl(self, a: int, z: int) -> float:
        return min(x.low for x in self.b[a:z + 1])


def _fresh_up(s: Series, t: int, level: float) -> bool:
    return s.b[t].close > level and s.b[t - 1].close <= level


def _fresh_dn(s: Series, t: int, level: float) -> bool:
    return s.b[t].close < level and s.b[t - 1].close >= level


# ---- double bottom / top -------------------------------------------------
def double_bottom(s: Series, t: int) -> Optional[Signal]:
    L = s.lows(t, 80)
    if len(L) < 2:
        return None
    (i1, p1), (i2, p2) = L[-2], L[-1]
    avg = (p1 + p2) / 2
    if i2 - i1 < 8 or abs(p1 - p2) / avg > 0.03 or t - i2 > 30:
        return None
    peak = s.maxh(i1, i2)
    if (peak - avg) / avg < 0.06 or s.minl(i2, t) < min(p1, p2) * 0.995:
        return None
    if not _fresh_up(s, t, peak):
        return None
    low = min(p1, p2)
    return Signal("double_bottom", "LONG", low, peak + (peak - low))


def double_top(s: Series, t: int) -> Optional[Signal]:
    H = s.highs(t, 80)
    if len(H) < 2:
        return None
    (i1, p1), (i2, p2) = H[-2], H[-1]
    avg = (p1 + p2) / 2
    if i2 - i1 < 8 or abs(p1 - p2) / avg > 0.03 or t - i2 > 30:
        return None
    trough = s.minl(i1, i2)
    if (avg - trough) / avg < 0.06 or s.maxh(i2, t) > max(p1, p2) * 1.005:
        return None
    if not _fresh_dn(s, t, trough):
        return None
    hi = max(p1, p2)
    return Signal("double_top", "SHORT", hi, trough - (hi - trough))


# ---- head & shoulders ----------------------------------------------------
def _argmin_low(s: Series, a: int, z: int) -> tuple[int, float]:
    j = min(range(a, z + 1), key=lambda x: s.b[x].low)
    return j, s.b[j].low


def _argmax_high(s: Series, a: int, z: int) -> tuple[int, float]:
    j = max(range(a, z + 1), key=lambda x: s.b[x].high)
    return j, s.b[j].high


def head_shoulders(s: Series, t: int) -> Optional[Signal]:
    H = s.highs(t, 100)
    if len(H) < 3:
        return None
    (i1, h1), (i2, h2), (i3, h3) = H[-3:]
    if not (h2 > h1 * 1.02 and h2 > h3 * 1.02) or abs(h1 - h3) / ((h1 + h3) / 2) > 0.06 or t - i3 > 25:
        return None
    if i2 - i1 < 5 or i3 - i2 < 5:
        return None
    j1, t1 = _argmin_low(s, i1, i2)
    j2, t2 = _argmin_low(s, i2, i3)
    if abs(t1 - t2) / ((t1 + t2) / 2) > 0.06 or j2 == j1:
        return None
    slope = (t2 - t1) / (j2 - j1)
    neck = lambda x: t1 + slope * (x - j1)
    if not (s.b[t].close < neck(t) and s.b[t - 1].close >= neck(t - 1)) or s.maxh(i3, t) > h3 * 1.001:
        return None
    height = h2 - (t1 + t2) / 2
    return Signal("head_shoulders", "SHORT", h3, neck(t) - height)


def inverse_head_shoulders(s: Series, t: int) -> Optional[Signal]:
    L = s.lows(t, 100)
    if len(L) < 3:
        return None
    (i1, l1), (i2, l2), (i3, l3) = L[-3:]
    if not (l2 < l1 * 0.98 and l2 < l3 * 0.98) or abs(l1 - l3) / ((l1 + l3) / 2) > 0.06 or t - i3 > 25:
        return None
    if i2 - i1 < 5 or i3 - i2 < 5:
        return None
    j1, p1 = _argmax_high(s, i1, i2)
    j2, p2 = _argmax_high(s, i2, i3)
    if abs(p1 - p2) / ((p1 + p2) / 2) > 0.06 or j2 == j1:
        return None
    slope = (p2 - p1) / (j2 - j1)
    neck = lambda x: p1 + slope * (x - j1)
    if not (s.b[t].close > neck(t) and s.b[t - 1].close <= neck(t - 1)) or s.minl(i3, t) < l3 * 0.999:
        return None
    height = (p1 + p2) / 2 - l2
    return Signal("inverse_head_shoulders", "LONG", l3, neck(t) + height)


# ---- triangles -----------------------------------------------------------
def ascending_triangle(s: Series, t: int) -> Optional[Signal]:
    H, L = s.highs(t, 60), s.lows(t, 60)
    if len(H) < 2 or len(L) < 2:
        return None
    H, L = H[-3:], L[-3:]
    hp = [p for _, p in H]
    if (max(hp) - min(hp)) / (sum(hp) / len(hp)) > 0.015:
        return None
    lp = [p for _, p in L]
    if not all(lp[i] < lp[i + 1] * 0.998 for i in range(len(lp) - 1)):
        return None
    start = min(H[0][0], L[0][0])
    if H[-1][0] - H[0][0] < 10 or t - start > 60:
        return None
    res = max(hp)
    if not _fresh_up(s, t, res) or s.minl(L[-1][0], t - 1) < lp[-1] * 0.995:
        return None
    return Signal("ascending_triangle", "LONG", lp[-1], res + (res - lp[0]))


def descending_triangle(s: Series, t: int) -> Optional[Signal]:
    H, L = s.highs(t, 60), s.lows(t, 60)
    if len(H) < 2 or len(L) < 2:
        return None
    H, L = H[-3:], L[-3:]
    lp = [p for _, p in L]
    if (max(lp) - min(lp)) / (sum(lp) / len(lp)) > 0.015:
        return None
    hp = [p for _, p in H]
    if not all(hp[i] > hp[i + 1] * 1.002 for i in range(len(hp) - 1)):
        return None
    start = min(H[0][0], L[0][0])
    if L[-1][0] - L[0][0] < 10 or t - start > 60:
        return None
    sup = min(lp)
    if not _fresh_dn(s, t, sup) or s.maxh(H[-1][0], t - 1) > hp[-1] * 1.005:
        return None
    return Signal("descending_triangle", "SHORT", hp[-1], sup - (hp[0] - sup))


# ---- flags -----------------------------------------------------------------
def bull_flag(s: Series, t: int) -> Optional[Signal]:
    for f in range(4, 13):
        st = t - f
        if st - 12 < 0:
            return None
        pole_top = s.maxh(st - 3, st)
        pole_bot = s.minl(st - 12, st - 3)
        if pole_top / pole_bot - 1 < 0.10:
            continue
        fh, fl = s.maxh(st + 1, t - 1), s.minl(st + 1, t - 1)
        if (pole_top - fl) / (pole_top - pole_bot) > 0.5 or (fh - fl) / fh > 0.08 or fh > pole_top * 1.03:
            continue
        cl = [x.close for x in s.b[st + 1:t]]
        third = max(1, len(cl) // 3)
        if sum(cl[-third:]) / third > sum(cl[:third]) / third:
            continue  # flag must drift flat/down
        if _fresh_up(s, t, fh):
            return Signal("bull_flag", "LONG", fl, fh + (pole_top - pole_bot))
    return None


def bear_flag(s: Series, t: int) -> Optional[Signal]:
    for f in range(4, 13):
        st = t - f
        if st - 12 < 0:
            return None
        pole_bot = s.minl(st - 3, st)
        pole_top = s.maxh(st - 12, st - 3)
        if 1 - pole_bot / pole_top < 0.10:
            continue
        fh, fl = s.maxh(st + 1, t - 1), s.minl(st + 1, t - 1)
        if (fh - pole_bot) / (pole_top - pole_bot) > 0.5 or (fh - fl) / fh > 0.08 or fl < pole_bot * 0.97:
            continue
        cl = [x.close for x in s.b[st + 1:t]]
        third = max(1, len(cl) // 3)
        if sum(cl[-third:]) / third < sum(cl[:third]) / third:
            continue
        if _fresh_dn(s, t, fl):
            return Signal("bear_flag", "SHORT", fh, fl - (pole_top - pole_bot))
    return None


# ---- cup & handle ----------------------------------------------------------
def cup_handle(s: Series, t: int) -> Optional[Signal]:
    if t < 130:
        return None
    for h in range(5, 16):
        hs = t - h
        hh, hl = s.maxh(hs, t - 1), s.minl(hs, t - 1)
        li, rim = _argmax_high(s, t - 130, hs - 15)
        ci, low = _argmin_low(s, li, hs - 1)
        depth = (rim - low) / rim
        if not (0.12 <= depth <= 0.35) or ci - li < 15 or hs - ci < 10:
            continue
        if hh < rim * 0.95 or hh > rim * 1.03 or hl < low + 0.5 * (rim - low) or (hh - hl) / hh > 0.12:
            continue
        if _fresh_up(s, t, hh):
            return Signal("cup_handle", "LONG", hl, hh + (rim - low))
    return None


# ---- additional chart patterns ----------------------------------------------
def _line(p1: tuple[int, float], p2: tuple[int, float]) -> Callable[[float], float]:
    (i1, v1), (i2, v2) = p1, p2
    return lambda x: v1 + (v2 - v1) * (x - i1) / (i2 - i1)


def _triple(s: Series, t: int, bottom: bool) -> Optional[Signal]:
    P_ = s.lows(t, 100) if bottom else s.highs(t, 100)
    if len(P_) < 3:
        return None
    (i1, a), (i2, b), (i3, c) = P_[-3:]
    mean_ = (a + b + c) / 3
    if i2 - i1 < 6 or i3 - i2 < 6 or (max(a, b, c) - min(a, b, c)) / mean_ > 0.03 or t - i3 > 30:
        return None
    if bottom:
        neck = max(s.maxh(i1, i2), s.maxh(i2, i3))
        if (neck - mean_) / mean_ < 0.05 or s.minl(i3, t) < min(a, b, c) * 0.995 or not _fresh_up(s, t, neck):
            return None
        return Signal("triple_bottom", "LONG", min(a, b, c), neck + (neck - mean_))
    neck = min(s.minl(i1, i2), s.minl(i2, i3))
    if (mean_ - neck) / mean_ < 0.05 or s.maxh(i3, t) > max(a, b, c) * 1.005 or not _fresh_dn(s, t, neck):
        return None
    return Signal("triple_top", "SHORT", max(a, b, c), neck - (mean_ - neck))


def triple_bottom(s: Series, t: int) -> Optional[Signal]:
    return _triple(s, t, True)


def triple_top(s: Series, t: int) -> Optional[Signal]:
    return _triple(s, t, False)


def _converging(s: Series, t: int):
    """Last three pivot highs/lows with trendlines; None unless the lines converge (<=70% of start width)."""
    H, L = s.highs(t, 90), s.lows(t, 90)
    if len(H) < 3 or len(L) < 3:
        return None
    H, L = H[-3:], L[-3:]
    start = min(H[0][0], L[0][0])
    if t - start < 15 or t - start > 90:
        return None
    U, Lo = _line(H[0], H[-1]), _line(L[0], L[-1])
    w0, w1 = U(start) - Lo(start), U(t) - Lo(t)
    if w0 <= 0 or w1 <= 0 or w1 > 0.7 * w0:
        return None
    return H, L, U, Lo, w0, start


def _desc(v): return all(v[i] > v[i + 1] * 1.002 for i in range(len(v) - 1))
def _asc(v): return all(v[i] < v[i + 1] * 0.998 for i in range(len(v) - 1))


def falling_wedge(s: Series, t: int) -> Optional[Signal]:
    c = _converging(s, t)
    if not c:
        return None
    H, L, U, Lo, w0, start = c
    if not (_desc([p for _, p in H]) and _desc([p for _, p in L])) or not _fresh_up_line(s, t, U):
        return None
    return Signal("falling_wedge", "LONG", L[-1][1], U(t) + w0)


def rising_wedge(s: Series, t: int) -> Optional[Signal]:
    c = _converging(s, t)
    if not c:
        return None
    H, L, U, Lo, w0, start = c
    if not (_asc([p for _, p in H]) and _asc([p for _, p in L])) or not _fresh_dn_line(s, t, Lo):
        return None
    return Signal("rising_wedge", "SHORT", H[-1][1], Lo(t) - w0)


def _fresh_up_line(s: Series, t: int, f) -> bool:
    return s.b[t].close > f(t) and s.b[t - 1].close <= f(t - 1)


def _fresh_dn_line(s: Series, t: int, f) -> bool:
    return s.b[t].close < f(t) and s.b[t - 1].close >= f(t - 1)


def symmetrical_triangle_up(s: Series, t: int) -> Optional[Signal]:
    c = _converging(s, t)
    if not c:
        return None
    H, L, U, Lo, w0, start = c
    if not (_desc([p for _, p in H]) and _asc([p for _, p in L])) or not _fresh_up_line(s, t, U):
        return None
    return Signal("symmetrical_triangle_up", "LONG", L[-1][1], U(t) + w0)


def symmetrical_triangle_down(s: Series, t: int) -> Optional[Signal]:
    c = _converging(s, t)
    if not c:
        return None
    H, L, U, Lo, w0, start = c
    if not (_desc([p for _, p in H]) and _asc([p for _, p in L])) or not _fresh_dn_line(s, t, Lo):
        return None
    return Signal("symmetrical_triangle_down", "SHORT", H[-1][1], Lo(t) - w0)


def _rectangle(s: Series, t: int):
    H, L = s.highs(t, 80), s.lows(t, 80)
    if len(H) < 2 or len(L) < 2:
        return None
    H, L = H[-3:], L[-3:]
    hp, lp = [p for _, p in H], [p for _, p in L]
    if (max(hp) - min(hp)) / (sum(hp) / len(hp)) > 0.015 or (max(lp) - min(lp)) / (sum(lp) / len(lp)) > 0.015:
        return None
    res, sup = max(hp), min(lp)
    start = min(H[0][0], L[0][0])
    if not (0.03 <= (res - sup) / sup <= 0.15) or t - start < 15:
        return None
    return res, sup, start


def rectangle_up(s: Series, t: int) -> Optional[Signal]:
    r = _rectangle(s, t)
    if not r:
        return None
    res, sup, start = r
    if not _fresh_up(s, t, res) or s.minl(start, t - 1) < sup * 0.99:
        return None
    return Signal("rectangle_up", "LONG", sup, res + (res - sup))


def rectangle_down(s: Series, t: int) -> Optional[Signal]:
    r = _rectangle(s, t)
    if not r:
        return None
    res, sup, start = r
    if not _fresh_dn(s, t, sup) or s.maxh(start, t - 1) > res * 1.01:
        return None
    return Signal("rectangle_down", "SHORT", res, sup - (res - sup))


def _pennant(s: Series, t: int, bull: bool) -> Optional[Signal]:
    for f in range(5, 16):
        st = t - f
        if st - 12 < 0:
            return None
        mid = st + 1 + (f - 1) // 2
        if bull:
            pole_top, pole_bot = s.maxh(st - 3, st), s.minl(st - 12, st - 3)
            if pole_top / pole_bot - 1 < 0.10:
                continue
        else:
            pole_bot, pole_top = s.minl(st - 3, st), s.maxh(st - 12, st - 3)
            if 1 - pole_bot / pole_top < 0.10:
                continue
        fh, fl = s.maxh(st + 1, t - 1), s.minl(st + 1, t - 1)
        r1 = s.maxh(st + 1, mid) - s.minl(st + 1, mid)
        r2 = s.maxh(mid + 1, t - 1) - s.minl(mid + 1, t - 1)
        if r1 <= 0 or r2 > 0.67 * r1 or (fh - fl) / fh > 0.10:
            continue
        if bull and (pole_top - fl) / (pole_top - pole_bot) <= 0.5 and _fresh_up(s, t, fh):
            return Signal("bull_pennant", "LONG", fl, fh + (pole_top - pole_bot))
        if not bull and (fh - pole_bot) / (pole_top - pole_bot) <= 0.5 and _fresh_dn(s, t, fl):
            return Signal("bear_pennant", "SHORT", fh, fl - (pole_top - pole_bot))
    return None


def bull_pennant(s: Series, t: int) -> Optional[Signal]:
    return _pennant(s, t, True)


def bear_pennant(s: Series, t: int) -> Optional[Signal]:
    return _pennant(s, t, False)


def rounding_bottom(s: Series, t: int) -> Optional[Signal]:
    W = 60
    if t < W + 5:
        return None
    seg = s.b[t - W:t]
    closes = [x.close for x in seg]
    imin = min(range(W), key=lambda i: closes[i])
    if not (0.3 * W <= imin <= 0.7 * W):
        return None
    left, right = max(closes[:10]), max(closes[-10:])
    rim = min(left, right)
    depth = (rim - closes[imin]) / rim
    if not (0.08 <= depth <= 0.30) or abs(left - right) / left > 0.06:
        return None
    trigger = s.maxh(t - 10, t - 1)
    if not _fresh_up(s, t, trigger):
        return None
    return Signal("rounding_bottom", "LONG", s.minl(t - 15, t - 1), trigger + (rim - closes[imin]))


# ---- candlestick reversals (frozen simple definitions, daily bars) ----------------
def _ret(s: Series, a: int, z: int) -> float:
    return s.b[z].close / s.b[a].close - 1


def _body(b): return abs(b.close - b.open)
def _rng(b): return b.high - b.low


def _two_r(side: str, name: str, close: float, stop: float) -> Optional[Signal]:
    risk = abs(close - stop)
    if risk <= 0:
        return None
    return Signal(name, side, stop, close + 2 * risk if side == "LONG" else close - 2 * risk)


def bullish_engulfing(s: Series, t: int) -> Optional[Signal]:
    if t < 8:
        return None
    a, b = s.b[t - 1], s.b[t]
    if _ret(s, t - 7, t - 2) > -0.03 or not (a.close < a.open and b.close > b.open):
        return None
    if not (b.open <= a.close and b.close >= a.open):
        return None
    return _two_r("LONG", "bullish_engulfing", b.close, min(a.low, b.low))


def bearish_engulfing(s: Series, t: int) -> Optional[Signal]:
    if t < 8:
        return None
    a, b = s.b[t - 1], s.b[t]
    if _ret(s, t - 7, t - 2) < 0.03 or not (a.close > a.open and b.close < b.open):
        return None
    if not (b.open >= a.close and b.close <= a.open):
        return None
    return _two_r("SHORT", "bearish_engulfing", b.close, max(a.high, b.high))


def hammer(s: Series, t: int) -> Optional[Signal]:
    if t < 8:
        return None
    b = s.b[t]
    r = _rng(b)
    if r <= 0 or _ret(s, t - 6, t - 1) > -0.03:
        return None
    lower, upper = min(b.open, b.close) - b.low, b.high - max(b.open, b.close)
    if _body(b) > 0.3 * r or lower < max(2 * _body(b), 0.6 * r) or upper > 0.2 * r:
        return None
    return _two_r("LONG", "hammer", b.close, b.low)


def shooting_star(s: Series, t: int) -> Optional[Signal]:
    if t < 8:
        return None
    b = s.b[t]
    r = _rng(b)
    if r <= 0 or _ret(s, t - 6, t - 1) < 0.03:
        return None
    lower, upper = min(b.open, b.close) - b.low, b.high - max(b.open, b.close)
    if _body(b) > 0.3 * r or upper < max(2 * _body(b), 0.6 * r) or lower > 0.2 * r:
        return None
    return _two_r("SHORT", "shooting_star", b.close, b.high)


def morning_star(s: Series, t: int) -> Optional[Signal]:
    if t < 9:
        return None
    a, m, c = s.b[t - 2], s.b[t - 1], s.b[t]
    if _ret(s, t - 8, t - 3) > -0.03 or _rng(a) <= 0 or _rng(m) <= 0 or _rng(c) <= 0:
        return None
    if not (a.close < a.open and _body(a) >= 0.6 * _rng(a) and _body(m) <= 0.3 * _rng(m)
            and c.close > c.open and c.close >= (a.open + a.close) / 2):
        return None
    return _two_r("LONG", "morning_star", c.close, min(a.low, m.low, c.low))


def evening_star(s: Series, t: int) -> Optional[Signal]:
    if t < 9:
        return None
    a, m, c = s.b[t - 2], s.b[t - 1], s.b[t]
    if _ret(s, t - 8, t - 3) < 0.03 or _rng(a) <= 0 or _rng(m) <= 0 or _rng(c) <= 0:
        return None
    if not (a.close > a.open and _body(a) >= 0.6 * _rng(a) and _body(m) <= 0.3 * _rng(m)
            and c.close < c.open and c.close <= (a.open + a.close) / 2):
        return None
    return _two_r("SHORT", "evening_star", c.close, max(a.high, m.high, c.high))


# ---- remaining clear-rule patterns from the cheat sheet -------------------------
def inverse_cup_handle(s: Series, t: int) -> Optional[Signal]:
    """Mirror of cup_handle: dome top, rim retest with a small bounce, break DOWN through the handle low."""
    if t < 130:
        return None
    for h in range(5, 16):
        hs = t - h
        hh, hl = s.maxh(hs, t - 1), s.minl(hs, t - 1)
        li, rim = _argmin_low(s, t - 130, hs - 15)
        ci, top = _argmax_high(s, li, hs - 1)
        depth = (top - rim) / top
        if not (0.12 <= depth <= 0.35) or ci - li < 15 or hs - ci < 10:
            continue
        if hl > rim * 1.05 or hl < rim * 0.97 or hh > top - 0.5 * (top - rim) or (hh - hl) / hh > 0.12:
            continue
        if _fresh_dn(s, t, hl):
            return Signal("inverse_cup_handle", "SHORT", hh, hl - (top - rim))
    return None


def rounding_top(s: Series, t: int) -> Optional[Signal]:
    W = 60
    if t < W + 5:
        return None
    closes = [x.close for x in s.b[t - W:t]]
    imax = max(range(W), key=lambda i: closes[i])
    if not (0.3 * W <= imax <= 0.7 * W):
        return None
    left, right = min(closes[:10]), min(closes[-10:])
    rim = max(left, right)
    depth = (closes[imax] - rim) / closes[imax]
    if not (0.08 <= depth <= 0.30) or abs(left - right) / left > 0.06:
        return None
    trigger = s.minl(t - 10, t - 1)
    if not _fresh_dn(s, t, trigger):
        return None
    return Signal("rounding_top", "SHORT", s.maxh(t - 15, t - 1), trigger - (closes[imax] - rim))


def _island(s: Series, t: int, bullish: bool) -> Optional[Signal]:
    """Gap away from a trend, 1-5 bars on the 'island', gap back. Signal on the closing-gap bar."""
    b = s.b[t]
    for n in range(1, 6):
        a0 = t - n          # first island bar
        if a0 < 8:
            return None
        pre = s.b[a0 - 1]
        isl = s.b[a0:t]
        if bullish:
            if not (isl[0].high < pre.low and b.low > max(x.high for x in isl)):
                continue
            if _ret(s, a0 - 7, a0 - 1) > -0.03 or b.close <= b.open:
                continue
            return _two_r("LONG", "island_reversal_bullish", b.close, min(x.low for x in isl))
        if not (isl[0].low > pre.high and b.high < min(x.low for x in isl)):
            continue
        if _ret(s, a0 - 7, a0 - 1) < 0.03 or b.close >= b.open:
            continue
        return _two_r("SHORT", "island_reversal_bearish", b.close, max(x.high for x in isl))
    return None


def island_reversal_bullish(s: Series, t: int) -> Optional[Signal]:
    return _island(s, t, True)


def island_reversal_bearish(s: Series, t: int) -> Optional[Signal]:
    return _island(s, t, False)


def _broadening(s: Series, t: int):
    """Diverging trendlines: three rising highs and three falling lows, width growing >= 30%."""
    H, L = s.highs(t, 90), s.lows(t, 90)
    if len(H) < 3 or len(L) < 3:
        return None
    H, L = H[-3:], L[-3:]
    start = min(H[0][0], L[0][0])
    if t - start < 15 or t - start > 90:
        return None
    if not (_asc([p for _, p in H]) and _desc([p for _, p in L])):
        return None
    w0 = _line(H[0], H[-1])(start) - _line(L[0], L[-1])(start)
    w1 = H[-1][1] - L[-1][1]
    if w0 <= 0 or w1 < 1.3 * w0:
        return None
    return H, L, w1


def megaphone_up(s: Series, t: int) -> Optional[Signal]:
    c = _broadening(s, t)
    if not c:
        return None
    H, L, w = c
    if not _fresh_up(s, t, H[-1][1]):
        return None
    return Signal("megaphone_up", "LONG", L[-1][1], H[-1][1] + 0.5 * w)


def megaphone_down(s: Series, t: int) -> Optional[Signal]:
    c = _broadening(s, t)
    if not c:
        return None
    H, L, w = c
    if not _fresh_dn(s, t, L[-1][1]):
        return None
    return Signal("megaphone_down", "SHORT", H[-1][1], L[-1][1] - 0.5 * w)


DETECTORS: dict[str, Callable[[Series, int], Optional[Signal]]] = {
    f.__name__: f for f in (double_bottom, double_top, head_shoulders, inverse_head_shoulders,
                            ascending_triangle, descending_triangle, bull_flag, bear_flag, cup_handle,
                            triple_bottom, triple_top, falling_wedge, rising_wedge, symmetrical_triangle_up,
                            symmetrical_triangle_down, rectangle_up, rectangle_down, bull_pennant, bear_pennant,
                            rounding_bottom, bullish_engulfing, bearish_engulfing, hammer, shooting_star,
                            morning_star, evening_star, inverse_cup_handle, rounding_top, island_reversal_bullish,
                            island_reversal_bearish, megaphone_up, megaphone_down)}


# ---- event day (volume spike + big move): trade WITH the move --------------
def event_day(s: Series, t: int) -> Optional[Signal]:
    """Frozen rule from scripts/run_events.py: volume >= 2.5x prior-20d mean and |close/prev - 1| >= 4%.
    Stop = the event bar's opposite extreme; target = 2R from the event close; hold <= 20 days."""
    if t < 25:
        return None
    b, p = s.b[t], s.b[t - 1]
    vavg = sum(x.volume for x in s.b[t - 20:t]) / 20
    ret = b.close / p.close - 1
    if vavg <= 0 or b.volume < 2.5 * vavg or abs(ret) < 0.04:
        return None
    if ret > 0:
        risk = b.close - b.low
        return Signal("event_day", "LONG", b.low, b.close + 2 * risk) if risk > 0 else None
    risk = b.high - b.close
    return Signal("event_day", "SHORT", b.high, b.close - 2 * risk) if risk > 0 else None


EVENT_DETECTORS: dict[str, Callable[[Series, int], Optional[Signal]]] = {"event_day": event_day}
HOLD_DAYS = {"event_day": 20, "bullish_engulfing": 10, "bearish_engulfing": 10, "hammer": 10,
             "shooting_star": 10, "morning_star": 10, "evening_star": 10,
             "island_reversal_bullish": 10, "island_reversal_bearish": 10}  # default 30


# ---- trade simulation ------------------------------------------------------
@dataclass
class PTrade:
    symbol: str
    pattern: str
    side: str
    signal_date: datetime
    entry: float
    stop: float
    target: float
    exit: float = 0.0
    reason: str = ""
    r: float = 0.0
    bars_held: int = 0


def simulate_trade(symbol: str, s: "Series", t: int, name: str, side: str, stop: float, target: float,
                   slippage_bps: float = 2.0, max_hold: int = 30, min_rr: float = 1.0) -> Optional[PTrade]:
    """Signal on bar t's close; enter at t+1 open. None if the setup is invalid at entry."""
    long = side == "LONG"
    d = 1 if long else -1
    slip = slippage_bps / 1e4
    n = len(s.b)
    if t + 1 >= n:
        return None
    e = s.b[t + 1]
    entry = e.open * (1 + d * slip)
    bad = (entry <= stop or e.open >= target) if long else (entry >= stop or e.open <= target)
    risk = abs(entry - stop)
    if bad or risk <= 0 or abs(target - entry) < min_rr * risk:
        return None
    tr = PTrade(symbol, name, side, s.b[t].ts, entry, stop, target)
    j = t + 1
    while True:
        b = s.b[j]
        gap_stop = (b.open <= stop) if long else (b.open >= stop)
        hit_stop = (b.low <= stop) if long else (b.high >= stop)
        hit_tgt = (b.high >= target) if long else (b.low <= target)
        if j > t + 1 and gap_stop:
            px, reason, mkt = b.open, "stop_gap", True
        elif hit_stop:
            px, reason, mkt = stop, "stop", True
        elif hit_tgt:
            px, reason, mkt = target, "target", False
        elif j - (t + 1) + 1 >= max_hold or j == n - 1:
            px, reason, mkt = b.close, "time", True
        else:
            j += 1
            continue
        break
    px = px * (1 - d * slip) if mkt else px
    tr.exit, tr.reason, tr.bars_held = px, reason, j - t
    tr.r = (px - entry) * d / risk
    tr.exit_index = j  # type: ignore[attr-defined]
    return tr


def backtest_pattern(symbol: str, bars: Sequence[Bar], name: str, slippage_bps: float = 2.0,
                     max_hold: Optional[int] = None, min_rr: float = 1.0, warmup: int = 140) -> list[PTrade]:
    max_hold = max_hold or HOLD_DAYS.get(name, 30)
    s = Series(bars)
    det = DETECTORS[name]
    n = len(s.b)
    out: list[PTrade] = []
    t = warmup
    while t < n - 2:
        sig = det(s, t)
        tr = simulate_trade(symbol, s, t, name, sig.side, sig.stop, sig.target, slippage_bps, max_hold, min_rr) if sig else None
        if tr is None:
            t += 1
            continue
        tr.signal_index = t  # type: ignore[attr-defined]
        out.append(tr)
        t = tr.exit_index + 1  # type: ignore[attr-defined]
    return out
