"""Live/paper daily runner for the ORB strategy.

Call `step(now)` once per bar close (e.g. each minute). It mirrors
`orb.run_day` so live behavior matches what was backtested:
  signal on a completed bar's close -> plan -> quote attach -> risk gate ->
  bracket order. Flat at `flat_time`; stops for the day at the profit target,
loss limit, or trade cap. All I/O is injected, so it runs against fakes.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from .market_data import MarketDataAdapter
from .models import (AssetClass, Confidence, Decision, Mode, Side, TradePlan)
from .orb import ET, OPEN, Bar, ORBConfig, _size, _t

log = logging.getLogger("bull_brain.runner")

BarsSource = Callable[[str, datetime, datetime], list[Bar]]


@dataclass
class StepResult:
    action: str          # WAIT | NO_DATA | SIGNAL_SUBMITTED | SIGNAL_REJECTED | SKIP | FLATTENED | DONE
    detail: str = ""
    plan_id: Optional[str] = None


@dataclass
class _DayState:
    day: date
    or_hi: Optional[float] = None
    or_lo: Optional[float] = None
    armed: dict = field(default_factory=dict)
    attempts: int = 0
    last_bar: Optional[datetime] = None
    flattened: bool = False
    done: bool = False


class ORBRunner:
    def __init__(self, symbol: str, cfg: ORBConfig, bars_source: BarsSource,
                 adapter: MarketDataAdapter, broker, venue: str = "ALPACA"):
        self.symbol, self.cfg, self.bars_source = symbol, cfg, bars_source
        self.adapter, self.broker, self.venue = adapter, broker, venue
        self.state: Optional[_DayState] = None

    # -- helpers -------------------------------------------------------
    def _new_day(self, day: date) -> _DayState:
        return _DayState(day, armed={"LONG": True, "SHORT": self.cfg.allow_short})

    def _plan(self, side: str, quote, or_hi: float, or_lo: float, equity: float, n: int,
              bar: Bar) -> tuple[Optional[TradePlan], str]:
        long = side == "LONG"
        entry = quote.ask if long else quote.bid
        stop = or_lo if long else or_hi
        if (entry <= stop) if long else (entry >= stop):
            return None, "price already through the stop level"
        stop_pct = abs(entry - stop) / entry
        if not (self.cfg.min_stop_pct <= stop_pct <= self.cfg.max_stop_pct):
            return None, f"stop distance {stop_pct:.4%} outside configured bounds"
        qty = _size(self.cfg, equity, entry, stop)
        if qty <= 0:
            return None, "position size rounds to zero"
        rng = f"{or_lo:.2f}-{or_hi:.2f}"
        return TradePlan(
            plan_id=f"orb-{self.symbol}-{self.state.day:%Y%m%d}-{n}", mode=Mode.PAPER,
            instrument=self.symbol, asset_class=AssetClass.EQUITY, venue=self.venue,
            side=Side.LONG if long else Side.SHORT, horizon="intraday", entry=entry, stop=stop,
            quantity=qty, quote=quote,
            thesis=f"{self.cfg.or_minutes}-min opening range {rng} broken {'up' if long else 'down'} on close {bar.close:.2f}",
            opposing_thesis="failed breakout: price reverses back into the range (chop day)",
            invalidation=f"price trades {'below' if long else 'above'} {stop:.2f}",
            confidence=Confidence.LOW), ""

    # -- main entry ----------------------------------------------------
    def step(self, now: Optional[datetime] = None) -> StepResult:
        now = (now or datetime.now(timezone.utc)).astimezone(ET)
        cfg = self.cfg
        if self.state is None or self.state.day != now.date():
            self.state = self._new_day(now.date())
        st = self.state
        if st.done:
            return StepResult("DONE", "day complete")
        open_dt = datetime.combine(now.date(), OPEN, tzinfo=ET)
        if now < open_dt:
            return StepResult("WAIT", "market not open")

        # flatten at flat_time
        if now.time() >= cfg.flat_time:
            if not st.flattened:
                st.flattened = True
                if self.broker.has_position(self.symbol):
                    self.broker.flatten(self.symbol)
                    st.done = True
                    return StepResult("FLATTENED", "flat time reached")
            st.done = True
            return StepResult("DONE", "past flat time")

        bars = self.bars_source(self.symbol, open_dt, now)
        done_bars = sorted((b for b in bars if b.ts + timedelta(minutes=cfg.bar_minutes) <= now),
                           key=lambda b: b.ts)
        or_end = open_dt + timedelta(minutes=cfg.or_minutes)
        or_bars = [b for b in done_bars if b.ts < or_end]
        if not done_bars:
            return StepResult("NO_DATA", "no completed bars")
        if now < or_end:
            return StepResult("WAIT", "opening range forming")
        if len(or_bars) < cfg.or_minutes // cfg.bar_minutes or or_bars[0].ts != open_dt:
            st.done = True
            return StepResult("NO_DATA", "incomplete opening range; sitting out today")
        st.or_hi, st.or_lo = max(b.high for b in or_bars), min(b.low for b in or_bars)

        after = [b for b in done_bars if b.ts >= or_end]
        if not after:
            return StepResult("WAIT", "no post-range bar yet")
        bar = after[-1]
        if st.last_bar == bar.ts:
            return StepResult("WAIT", "bar already processed")
        st.last_bar = bar.ts

        if st.or_lo < bar.close < st.or_hi:  # re-arm once price is back inside
            st.armed = {"LONG": True, "SHORT": cfg.allow_short}

        acct = self.broker.account_state(self.symbol)
        if cfg.daily_profit_target is not None and acct.daily_pnl >= cfg.daily_profit_target:
            st.done = True
            return StepResult("DONE", f"daily profit target reached ({acct.daily_pnl:.2f})")
        if cfg.daily_loss_limit is not None and acct.daily_pnl <= -cfg.daily_loss_limit:
            st.done = True
            return StepResult("DONE", f"daily loss limit reached ({acct.daily_pnl:.2f})")
        if st.attempts >= cfg.max_trades_per_day:
            st.done = True
            return StepResult("DONE", "trade cap reached")
        if self.broker.has_position(self.symbol):
            return StepResult("WAIT", "position open")
        if now.time() >= cfg.last_entry:
            return StepResult("WAIT", "past last entry time")

        side = None
        if st.armed["LONG"] and bar.close > st.or_hi:
            side = "LONG"
        elif st.armed["SHORT"] and bar.close < st.or_lo:
            side = "SHORT"
        if side is None:
            return StepResult("WAIT", "no signal")

        quote, chk = self.adapter.fetch(self.symbol, self.venue, now)
        if quote is None or not chk.usable:
            return StepResult("NO_DATA", f"{side} signal but quote unusable: {chk.status.value} {chk.detail}")
        n = st.attempts + 1
        plan, why = self._plan(side, quote, st.or_hi, st.or_lo, acct.equity, n, bar)
        st.armed[side] = False
        if plan is None:
            return StepResult("SKIP", f"{side} signal skipped: {why}")
        st.attempts = n
        res = self.broker.submit(plan, now)
        if res.approved:
            log.info("submitted %s %s x%s", plan.plan_id, side, plan.quantity)
            return StepResult("SIGNAL_SUBMITTED", f"{side} {plan.quantity} @~{plan.entry:.2f}", plan.plan_id)
        if res.decision is Decision.HALT:
            st.done = True
        return StepResult("SIGNAL_REJECTED", f"{res.decision.value}: {'; '.join(res.reasons)}", plan.plan_id)


def run_loop(runner: ORBRunner, now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
             sleep_fn: Callable[[float], None] = _time.sleep,
             on_result: Callable[[datetime, StepResult], None] = lambda t, r: print(t.isoformat(), r.action, r.detail)):
    """Step once per minute (2s after the bar closes) until the day is done."""
    while True:
        now = now_fn()
        res = runner.step(now)
        on_result(now, res)
        if res.action in ("DONE", "FLATTENED") or (res.action == "NO_DATA" and runner.state and runner.state.done):
            return
        sleep_fn(60 - now.second + 2)
