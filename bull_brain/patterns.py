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


DETECTORS: dict[str, Callable[[Series, int], Optional[Signal]]] = {
    f.__name__: f for f in (double_bottom, double_top, head_shoulders, inverse_head_shoulders,
                            ascending_triangle, descending_triangle, bull_flag, bear_flag, cup_handle)}


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
                     max_hold: int = 30, min_rr: float = 1.0, warmup: int = 140) -> list[PTrade]:
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
