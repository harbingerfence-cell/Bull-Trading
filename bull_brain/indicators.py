"""Technical indicators computed from bars (Alpaca provides none).

Pure functions over lists of floats / Bars. Each returns None (or a list with
leading Nones) until there is enough history; never extrapolates.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .orb import Bar


def sma(values: Sequence[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def ema(values: Sequence[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n  # seed with SMA
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], n: int = 14) -> list[Optional[float]]:
    """Wilder's RSI."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n

    def val(g: float, l: float) -> float:
        return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    out[n] = val(ag, al)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = val(ag, al)
    return out


def atr(bars: Sequence[Bar], n: int = 14) -> list[Optional[float]]:
    """Wilder's average true range."""
    out: list[Optional[float]] = [None] * len(bars)
    if len(bars) <= n:
        return out
    tr = [bars[0].high - bars[0].low] + [
        max(bars[i].high - bars[i].low, abs(bars[i].high - bars[i - 1].close), abs(bars[i].low - bars[i - 1].close))
        for i in range(1, len(bars))]
    prev = sum(tr[1:n + 1]) / n
    out[n] = prev
    for i in range(n + 1, len(bars)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def session_vwap(bars: Sequence[Bar]) -> list[Optional[float]]:
    """Cumulative VWAP over the given bars (pass one session's bars)."""
    out: list[Optional[float]] = []
    pv = vv = 0.0
    for b in bars:
        pv += (b.high + b.low + b.close) / 3 * b.volume
        vv += b.volume
        out.append(pv / vv if vv > 0 else None)
    return out


def relative_volume(volume_today: float, volumes_history: Sequence[float]) -> Optional[float]:
    """Today's volume (to the same time of day) over the mean of prior days'."""
    if not volumes_history:
        return None
    avg = sum(volumes_history) / len(volumes_history)
    return volume_today / avg if avg > 0 else None
