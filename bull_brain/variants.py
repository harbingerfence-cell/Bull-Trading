"""Research engine: opening-range breakout vs fade, with VWAP / gap filters.

Generalises `orb.py` for experiments (stocks and crypto). Same honesty rules:
signal on a completed bar's close, fill at the NEXT bar's open, stop assumed
first when a bar spans stop and target, costs charged on entry and exit
(slippage on market-type exits: stop and time; target is a resting limit).
Sparse bars (minutes with no trades) are tolerated: a signal is dropped if
the next bar is more than 2 bar-lengths away. Research only; not an order path.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .orb import BacktestResult, Bar, Trade


@dataclass(frozen=True)
class VConfig:
    mode: str = "breakout"            # breakout | fade
    filters: tuple = ()               # subset of {"vwap", "gap"}; filters act on the TRADE side
    tz: str = "America/New_York"
    open_time: time = time(9, 30)
    or_minutes: int = 15
    hold_minutes: int = 90            # flat this long after open
    entry_window_minutes: int = 60    # no new entries after this
    target_r: float = 1.5             # breakout target (R multiples)
    stop_mult: float = 0.5            # fade stop beyond the range edge, in range-heights
    risk_fraction: float = 0.005
    max_notional_fraction: float = 4.0
    max_trades_per_day: int = 3
    slippage_bps: float = 1.0
    fee_bps: float = 0.0              # per side, on notional
    fractional: bool = False
    min_stop_pct: float = 0.0005
    max_stop_pct: float = 0.015
    min_or_bar_fraction: float = 0.6
    regular_close: time = time(16, 0)  # for prev-close (gap filter), stocks only


def _prev_closes(by_day: dict[date, list[Bar]], tz: ZoneInfo, close_t: time) -> dict[date, float]:
    out, last = {}, None
    for d in sorted(by_day):
        out[d] = last
        reg = [b for b in by_day[d] if b.ts.astimezone(tz).time() <= close_t]
        if reg:
            last = reg[-1].close
    return out


def _size(cfg: VConfig, equity: float, entry: float, stop: float) -> float:
    rps = abs(entry - stop) + entry * 2 * (cfg.fee_bps + cfg.slippage_bps) / 1e4
    if rps <= 0:
        return 0.0
    q = min(equity * cfg.risk_fraction / rps, equity * cfg.max_notional_fraction / entry)
    return math.floor(q * 1e6) / 1e6 if cfg.fractional else float(math.floor(q))


def simulate_day(bars: list[Bar], cfg: VConfig, equity: float,
                 prev_close: Optional[float] = None) -> tuple[list[Trade], Optional[str]]:
    tz = ZoneInfo(cfg.tz)
    bars = sorted(bars, key=lambda b: b.ts)
    day = bars[0].ts.astimezone(tz).date()
    open_dt = datetime.combine(day, cfg.open_time, tzinfo=tz)
    or_end = open_dt + timedelta(minutes=cfg.or_minutes)
    flat = open_dt + timedelta(minutes=cfg.hold_minutes)
    last_entry = open_dt + timedelta(minutes=cfg.entry_window_minutes)
    window = [b for b in bars if open_dt <= b.ts <= flat]
    or_bars = [b for b in window if b.ts < or_end]
    if len(or_bars) < max(1, math.ceil(cfg.min_or_bar_fraction * cfg.or_minutes)):
        return [], "incomplete opening range"
    hi, lo = max(b.high for b in or_bars), min(b.low for b in or_bars)
    if hi <= lo:
        return [], "degenerate range"
    mid, rng = (hi + lo) / 2, hi - lo
    first_open = or_bars[0].open
    if "gap" in cfg.filters and prev_close is None:
        return [], "no previous close for gap filter"
    gap_up = prev_close is not None and first_open > prev_close
    gap_dn = prev_close is not None and first_open < prev_close

    slip = cfg.slippage_bps / 1e4
    fee = cfg.fee_bps / 1e4
    gap_ok = lambda long: True if "gap" not in cfg.filters else (gap_up if long else gap_dn)

    trades: list[Trade] = []
    open_t: Optional[Trade] = None
    armed = True
    pend: Optional[tuple[str, datetime]] = None
    pv = vv = 0.0  # vwap accumulators

    def close(t: Trade, price: float, ts: datetime, reason: str, market: bool):
        d = 1 if t.side == "LONG" else -1
        px = price * (1 - d * slip) if market else price
        t.exit_time, t.exit, t.reason = ts, px, reason
        t.pnl = (px - t.entry) * d * t.shares - fee * t.shares * (t.entry + px)
        t.r_multiple = t.pnl / t.risk_dollars if t.risk_dollars else 0.0

    for i, b in enumerate(window):
        # enter pending signal at this bar's open
        if pend and open_t is None:
            side, sig_ts = pend
            pend = None
            if b.ts - sig_ts <= timedelta(minutes=2):
                long = side == "LONG"
                entry = b.open * (1 + slip) if long else b.open * (1 - slip)
                if cfg.mode == "breakout":
                    stop = lo if long else hi
                    risk = abs(entry - stop)
                    target = entry + cfg.target_r * risk if long else entry - cfg.target_r * risk
                    valid = entry > stop if long else entry < stop
                else:  # fade: stop beyond the broken edge, target = range midpoint
                    stop = lo - cfg.stop_mult * rng if long else hi + cfg.stop_mult * rng
                    target = mid
                    risk = abs(entry - stop)
                    valid = (entry > stop and target > entry) if long else (entry < stop and target < entry)
                stop_pct = abs(entry - stop) / entry
                q = _size(cfg, equity, entry, stop)
                if valid and q > 0 and cfg.min_stop_pct <= stop_pct <= cfg.max_stop_pct:
                    open_t = Trade(day, side, b.ts, entry, stop, target, q,  # type: ignore[arg-type]
                                   risk_dollars=q * (risk + entry * 2 * (cfg.fee_bps + cfg.slippage_bps) / 1e4))
                    trades.append(open_t)
                    armed = False
        # manage position
        if open_t is not None:
            t = open_t
            if t.side == "LONG":
                hs, ht = b.low <= t.stop, b.high >= t.target
            else:
                hs, ht = b.high >= t.stop, b.low <= t.target
            if hs:
                close(t, t.stop, b.ts, "stop", True); open_t = None
            elif ht:
                close(t, t.target, b.ts, "target", False); open_t = None
            elif b.ts >= flat:
                close(t, b.open, b.ts, "time", True); open_t = None
        # vwap on completed bar
        pv += (b.high + b.low + b.close) / 3 * b.volume
        vv += b.volume
        if b.ts < or_end or b.ts >= last_entry:
            continue
        if lo < b.close < hi:
            armed = True
        if open_t is not None or not armed or len(trades) >= cfg.max_trades_per_day:
            continue
        up, dn = b.close > hi, b.close < lo
        if not (up or dn):
            continue
        long = (up if cfg.mode == "breakout" else dn)
        if not gap_ok(long):
            continue
        if "vwap" in cfg.filters:
            vw = pv / vv if vv > 0 else None
            if vw is None or (b.close <= vw if long else b.close >= vw):
                continue
        pend = ("LONG" if long else "SHORT", b.ts)

    if open_t is not None and window:
        close(open_t, window[-1].close, window[-1].ts, "end_of_data", True)
    return trades, None


def backtest_v(bars: list[Bar], cfg: VConfig, equity: float) -> BacktestResult:
    tz = ZoneInfo(cfg.tz)
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_day[b.ts.astimezone(tz).date()].append(b)
    prevs = _prev_closes(by_day, tz, cfg.regular_close) if "gap" in cfg.filters else {}
    res = BacktestResult()
    for d in sorted(by_day):
        trades, skip = simulate_day(by_day[d], cfg, equity, prevs.get(d))
        if skip:
            res.skipped_days[d] = skip
            continue
        res.trades.extend(trades)
        res.daily_pnl[d] = sum(t.pnl for t in trades)
    return res


def split_by_date(bars: list[Bar], tz: str, train_fraction: float = 0.6) -> tuple[list[Bar], list[Bar]]:
    z = ZoneInfo(tz)
    days = sorted({b.ts.astimezone(z).date() for b in bars})
    cut = days[int(len(days) * train_fraction)]
    return ([b for b in bars if b.ts.astimezone(z).date() < cut],
            [b for b in bars if b.ts.astimezone(z).date() >= cut])
