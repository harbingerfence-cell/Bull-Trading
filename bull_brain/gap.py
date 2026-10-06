"""Gap strategies (research): trade the open gap after a 5-minute confirmation.

  go   : trade WITH the gap if the first `confirm_minutes` closed in the gap
         direction; stop at the confirm-window extreme, target = target_r x risk.
  fade : trade AGAINST the gap if the first window closed back toward the prior
         close; stop at the window extreme, target = prior close (gap fill).
Entry at the next bar's open after the window (no lookahead); stop assumed
first on ambiguous bars; costs on entry and market-type exits. R-multiples are
the primary metric (position size is just a scale factor).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from .orb import ET, Bar, Trade


@dataclass(frozen=True)
class GapConfig:
    mode: str = "go"                 # go | fade
    min_gap_pct: float = 0.01
    max_gap_pct: float = 0.10        # ignore giant (news/halt) gaps
    confirm_minutes: int = 5
    target_r: float = 2.0
    hold_until: time = time(15, 55)
    slippage_bps: float = 2.0
    equity: float = 100_000.0
    risk_fraction: float = 0.005
    min_stop_pct: float = 0.0015
    max_stop_pct: float = 0.03


def simulate_gap_day(bars: list[Bar], prev_close: Optional[float], cfg: GapConfig) -> Optional[Trade]:
    if prev_close is None or not bars:
        return None
    bars = sorted(bars, key=lambda b: b.ts)
    day = bars[0].ts.astimezone(ET).date()
    open_dt = datetime.combine(day, time(9, 30), tzinfo=ET)
    end_dt = datetime.combine(day, cfg.hold_until, tzinfo=ET)
    conf_end = open_dt + timedelta(minutes=cfg.confirm_minutes)
    win = [b for b in bars if open_dt <= b.ts < conf_end]
    if len(win) < 3 or win[0].ts != open_dt:
        return None
    gap = win[0].open / prev_close - 1
    if not (cfg.min_gap_pct <= abs(gap) <= cfg.max_gap_pct):
        return None
    up = gap > 0
    last_close = win[-1].close
    moved_with = last_close > win[0].open if up else last_close < win[0].open
    hi, lo = max(b.high for b in win), min(b.low for b in win)
    if cfg.mode == "go":
        if not moved_with:
            return None
        long = up
    else:
        if moved_with:
            return None
        long = not up
    rest = [b for b in bars if conf_end <= b.ts <= end_dt]
    if not rest or rest[0].ts - win[-1].ts > timedelta(minutes=3):
        return None
    slip = cfg.slippage_bps / 1e4
    entry = rest[0].open * (1 + slip) if long else rest[0].open * (1 - slip)
    stop = lo if long else hi
    if cfg.mode == "go":
        risk = abs(entry - stop)
        target = entry + cfg.target_r * risk if long else entry - cfg.target_r * risk
    else:
        target = prev_close
        risk = abs(entry - stop)
    ok = (entry > stop and target > entry) if long else (entry < stop and target < entry)
    stop_pct = abs(entry - stop) / entry
    if not ok or risk <= 0 or not (cfg.min_stop_pct <= stop_pct <= cfg.max_stop_pct):
        return None
    shares = cfg.equity * cfg.risk_fraction / (risk + entry * 2 * slip)
    t = Trade(day, "LONG" if long else "SHORT", rest[0].ts, entry, stop, target, shares,  # type: ignore[arg-type]
              risk_dollars=shares * (risk + entry * 2 * slip))
    d = 1 if long else -1
    for b in rest:
        hs = b.low <= stop if long else b.high >= stop
        ht = b.high >= target if long else b.low <= target
        if hs or ht:
            px, reason, market = (stop, "stop", True) if hs else (target, "target", False)
            break
    else:
        b, px, reason, market = rest[-1], rest[-1].close, "time", True
    px = px * (1 - d * slip) if market else px
    t.exit_time, t.exit, t.reason = b.ts, px, reason
    t.pnl = (px - entry) * d * shares
    t.r_multiple = t.pnl / t.risk_dollars
    return t


def gap_backtest(bars: list[Bar], cfg: GapConfig) -> list[Trade]:
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_day[b.ts.astimezone(ET).date()].append(b)
    out, prev = [], None
    for d in sorted(by_day):
        t = simulate_gap_day(by_day[d], prev, cfg)
        if t:
            out.append(t)
        reg = [b for b in by_day[d] if time(9, 30) <= b.ts.astimezone(ET).time() <= time(16, 0)]
        if reg:
            prev = reg[-1].close
    return out
