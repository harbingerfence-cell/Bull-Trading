"""Opening-range breakout (ORB) strategy + honest backtester.

Rules (all parameters in `ORBConfig`; changing them = a new strategy version):
  * Opening range (OR) = high/low of the first `or_minutes` after 09:30 ET.
  * Long when a bar CLOSES above OR high (short: below OR low); enter at the
    NEXT bar's open (no lookahead) plus slippage.
  * Stop at the opposite side of the range; target at `target_r` x risk.
  * Per-side re-entry only after price closes back inside the range ("re-arm").
  * At most `max_trades_per_day` (default 3). Optional daily profit target and
    daily loss limit stop trading for the day. Flat by `flat_time`.
  * Size = floor(equity * risk_fraction / (stop distance + costs)), capped by
    `max_notional_fraction` of equity.
  * If stop and target both fall inside one bar, the STOP is assumed first.
Days with an incomplete opening range are skipped, not guessed.

This is a hypothesis to be tested, not a proven edge.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)


@dataclass(frozen=True)
class Bar:
    ts: datetime  # bar START time, timezone-aware
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self):
        if self.ts.tzinfo is None:
            raise ValueError("bar timestamp must be timezone-aware")
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError(f"inconsistent OHLC at {self.ts}")


@dataclass(frozen=True)
class ORBConfig:
    or_minutes: int = 15
    bar_minutes: int = 1
    target_r: float = 1.5
    risk_fraction: float = 0.005        # of equity risked per trade
    max_notional_fraction: float = 1.0  # cash account: no leverage
    max_trades_per_day: int = 3
    last_entry: time = time(10, 30)
    flat_time: time = time(11, 0)
    daily_profit_target: Optional[float] = None   # currency; stop after reaching
    daily_loss_limit: Optional[float] = None      # currency; stop after reaching
    slippage_bps: float = 1.0
    cost_per_share: float = 0.0
    allow_short: bool = True
    min_stop_pct: float = 0.0005
    max_stop_pct: float = 0.015


@dataclass
class Trade:
    day: date
    side: str
    entry_time: datetime
    entry: float
    stop: float
    target: float
    shares: int
    exit_time: Optional[datetime] = None
    exit: Optional[float] = None
    reason: str = ""
    pnl: float = 0.0
    r_multiple: float = 0.0
    risk_dollars: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    daily_pnl: dict[date, float] = field(default_factory=dict)
    skipped_days: dict[date, str] = field(default_factory=dict)

    def summary(self, goal_low: float = 100.0) -> dict:
        n = len(self.trades)
        days = list(self.daily_pnl.values())
        wins = [t for t in self.trades if t.pnl > 0]
        eq, peak, mdd = 0.0, 0.0, 0.0
        for d in sorted(self.daily_pnl):
            eq += self.daily_pnl[d]
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
        return {
            "days_traded": len([p for d, p in self.daily_pnl.items()]),
            "days_skipped": len(self.skipped_days),
            "trades": n,
            "win_rate": len(wins) / n if n else None,
            "avg_r": sum(t.r_multiple for t in self.trades) / n if n else None,
            "total_pnl": sum(days),
            "avg_daily_pnl": sum(days) / len(days) if days else None,
            "pct_days_at_goal": sum(1 for p in days if p >= goal_low) / len(days) if days else None,
            "pct_down_days": sum(1 for p in days if p < 0) / len(days) if days else None,
            "worst_day": min(days) if days else None,
            "max_drawdown": mdd,
        }


def _t(ts: datetime) -> time:
    return ts.astimezone(ET).time()


def _size(cfg: ORBConfig, equity: float, entry: float, stop: float) -> int:
    rps = abs(entry - stop) + 2 * cfg.cost_per_share
    if rps <= 0:
        return 0
    by_risk = math.floor(equity * cfg.risk_fraction / rps)
    by_cap = math.floor(equity * cfg.max_notional_fraction / entry)
    return max(0, min(by_risk, by_cap))


def run_day(bars: list[Bar], cfg: ORBConfig, equity: float) -> tuple[list[Trade], Optional[str]]:
    """Run one session. Returns (trades, skip_reason)."""
    bars = sorted(bars, key=lambda b: b.ts)
    n_or = cfg.or_minutes // cfg.bar_minutes
    or_end_minutes = 9 * 60 + 30 + cfg.or_minutes
    or_bars = [b for b in bars if OPEN <= _t(b.ts) and _t(b.ts).hour * 60 + _t(b.ts).minute < or_end_minutes]
    if len(or_bars) < n_or or _t(or_bars[0].ts) != OPEN:
        return [], "incomplete opening range"
    or_hi, or_lo = max(b.high for b in or_bars), min(b.low for b in or_bars)
    if or_hi <= or_lo:
        return [], "degenerate opening range"
    rest = [b for b in bars if _t(b.ts).hour * 60 + _t(b.ts).minute >= or_end_minutes]

    trades: list[Trade] = []
    open_t: Optional[Trade] = None
    pending: Optional[str] = None
    armed = {"LONG": True, "SHORT": cfg.allow_short}
    realized = 0.0
    done = False
    slip = cfg.slippage_bps / 1e4

    def close(t: Trade, price: float, ts: datetime, reason: str):
        nonlocal realized
        direction = 1 if t.side == "LONG" else -1
        t.exit_time, t.exit, t.reason = ts, price, reason
        t.pnl = (price - t.entry) * direction * t.shares - 2 * cfg.cost_per_share * t.shares
        t.r_multiple = t.pnl / t.risk_dollars if t.risk_dollars else 0.0
        realized += t.pnl

    for b in rest:
        tm = _t(b.ts)
        # 1. enter pending signal at this bar's open (signal came from prior close)
        if pending and open_t is None and not done:
            long = pending == "LONG"
            entry = b.open * (1 + slip) if long else b.open * (1 - slip)
            stop = or_lo if long else or_hi
            stop_pct = abs(entry - stop) / entry
            shares = _size(cfg, equity, entry, stop)
            valid = (entry > stop if long else entry < stop) and cfg.min_stop_pct <= stop_pct <= cfg.max_stop_pct
            if valid and shares > 0:
                risk = abs(entry - stop)
                tgt = entry + cfg.target_r * risk if long else entry - cfg.target_r * risk
                open_t = Trade(b.ts.astimezone(ET).date(), pending, b.ts, entry, stop, tgt, shares,
                               risk_dollars=shares * (risk + 2 * cfg.cost_per_share))
                trades.append(open_t)
                armed[pending] = False
            pending = None

        # 2. manage open position on this bar (stop first if both touched)
        if open_t is not None:
            t = open_t
            if t.side == "LONG":
                hit_stop, hit_tgt = b.low <= t.stop, b.high >= t.target
            else:
                hit_stop, hit_tgt = b.high >= t.stop, b.low <= t.target
            if hit_stop:
                close(t, t.stop, b.ts, "stop")
                open_t = None
            elif hit_tgt:
                close(t, t.target, b.ts, "target")
                open_t = None
            elif tm >= cfg.flat_time:
                close(t, b.open, b.ts, "time")
                open_t = None

        # 3. daily rules
        if cfg.daily_profit_target is not None and realized >= cfg.daily_profit_target:
            done = True
        if cfg.daily_loss_limit is not None and realized <= -cfg.daily_loss_limit:
            done = True
        if len(trades) >= cfg.max_trades_per_day and open_t is None:
            done = True
        if tm >= cfg.flat_time:
            break
        if done and open_t is None:
            break

        # 4. look for a signal on this bar's close
        if b.close < or_hi and b.close > or_lo:
            armed = {"LONG": True, "SHORT": cfg.allow_short}  # re-arm: back inside range
        if open_t is None and not done and tm < cfg.last_entry and len(trades) < cfg.max_trades_per_day:
            if armed["LONG"] and b.close > or_hi:
                pending = "LONG"
            elif armed["SHORT"] and b.close < or_lo:
                pending = "SHORT"

    if open_t is not None and rest:  # data ended before flat time
        close(open_t, rest[-1].close, rest[-1].ts, "end_of_data")
    return trades, None


def backtest(bars: list[Bar], cfg: ORBConfig, equity: float) -> BacktestResult:
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        by_day[b.ts.astimezone(ET).date()].append(b)
    res = BacktestResult()
    for day in sorted(by_day):
        trades, skip = run_day(by_day[day], cfg, equity)
        if skip:
            res.skipped_days[day] = skip
            continue
        res.trades.extend(trades)
        res.daily_pnl[day] = sum(t.pnl for t in trades)
    return res
