"""Simulated broker. No network, no real orders.

Every order passes through the deterministic risk gate first. Duplicate
submissions of the same plan_id are rejected (idempotency). Fills are
conservative: longs buy the ask, shorts sell the bid, plus slippage and
per-unit costs. Only PAPER mode plans approved by the gate can fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .journal import Journal
from .models import AccountState, Decision, GateResult, Mode, RiskLimits, Side, TradePlan
from .risk_gate import evaluate


@dataclass
class Position:
    plan: TradePlan
    fill_price: float
    opened_at: datetime

    @property
    def signed_notional(self) -> float:
        n = self.plan.quantity * self.fill_price * self.plan.multiplier
        return n if self.plan.side is Side.LONG else -n


@dataclass
class ClosedTrade:
    plan_id: str
    pnl: float
    reason: str
    closed_at: datetime


class PaperBroker:
    def __init__(self, limits: RiskLimits, equity: float, journal: Optional[Journal] = None,
                 slippage_bps: float = 1.0):
        self.limits = limits
        self.start_equity = equity
        self.realized_pnl = 0.0
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.peak_equity = equity
        self.slippage_bps = slippage_bps
        self.journal = journal or Journal()
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self._seen: set[str] = set()

    # -- state ---------------------------------------------------------
    @property
    def equity(self) -> float:
        return self.start_equity + self.realized_pnl

    def account_state(self, instrument: Optional[str] = None) -> AccountState:
        gross = sum(abs(p.signed_notional) for p in self.positions.values())
        net = sum(p.signed_notional for p in self.positions.values())
        inst = sum(abs(p.signed_notional) for p in self.positions.values()
                   if p.plan.instrument == instrument)
        dd = max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
        return AccountState(equity=self.equity, daily_pnl=self.daily_pnl,
                            weekly_pnl=self.weekly_pnl, drawdown_fraction=min(dd, 1.0),
                            gross_exposure=gross, net_exposure=net, instrument_exposure=inst)

    def reset_day(self) -> None:
        self.daily_pnl = 0.0

    def reset_week(self) -> None:
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0

    # -- orders --------------------------------------------------------
    def submit(self, plan: TradePlan, now: Optional[datetime] = None) -> GateResult:
        now = now or datetime.now(timezone.utc)
        if plan.plan_id in self._seen:
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["duplicate plan_id: order already submitted"])
            self.journal.append("reject", plan.plan_id, {"reason": res.reasons[0]}, now)
            return res
        self._seen.add(plan.plan_id)
        self.journal.append("plan", plan.plan_id, plan.model_dump(mode="json"), now)

        if plan.mode is not Mode.PAPER:
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["paper broker only accepts PAPER mode plans"])
        else:
            res = evaluate(plan, self.limits, self.account_state(plan.instrument), now)
        self.journal.append("gate", plan.plan_id, res.model_dump(mode="json"), now)
        if not res.approved:
            return res

        q = plan.quote  # gate guarantees a fresh quote for approved plans
        slip = self.slippage_bps / 1e4
        price = q.ask * (1 + slip) if plan.side is Side.LONG else q.bid * (1 - slip)
        self.positions[plan.plan_id] = Position(plan, price, now)
        self.journal.append("fill", plan.plan_id,
                            {"price": price, "quantity": plan.quantity, "side": plan.side.value}, now)
        return res

    def close(self, plan_id: str, price: float, reason: str,
              now: Optional[datetime] = None) -> ClosedTrade:
        now = now or datetime.now(timezone.utc)
        pos = self.positions.pop(plan_id, None)
        if pos is None:
            raise KeyError(f"no open position for plan_id {plan_id!r}")
        p = pos.plan
        direction = 1 if p.side is Side.LONG else -1
        pnl = (price - pos.fill_price) * direction * p.quantity * p.multiplier \
            - 2 * p.per_unit_costs * p.quantity
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        trade = ClosedTrade(plan_id, pnl, reason, now)
        self.closed.append(trade)
        self.journal.append("exit", plan_id, {"price": price, "pnl": pnl, "reason": reason}, now)
        return trade

    def review(self, plan_id: str, notes: str, followed_process: bool,
               now: Optional[datetime] = None) -> None:
        self.journal.append("review", plan_id,
                            {"notes": notes, "followed_process": followed_process}, now)
