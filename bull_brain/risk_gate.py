"""Deterministic risk gate.

Pure function, no I/O, no model calls. Severity order: HALT > NEEDS_DATA >
NO_TRADE > pass. RESEARCH mode never approves execution; PAPER mode approves
only plans that clear every check; LIVE is blocked here by design.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from .models import (
    AccountState,
    AssetClass,
    Decision,
    GateResult,
    Mode,
    RiskLimits,
    TradePlan,
)


def evaluate(
    plan: TradePlan,
    limits: RiskLimits,
    account: AccountState,
    now: Optional[datetime] = None,
) -> GateResult:
    now = now or datetime.now(timezone.utc)
    halt: list[str] = []
    needs: list[str] = []
    block: list[str] = []
    missing: list[str] = []

    # 1. LIVE is disabled in this module.
    if plan.mode is Mode.LIVE:
        halt.append("LIVE mode is disabled; a separate authorized execution system is required")

    # 2. Unset owner limits block execution.
    unset = limits.unset_fields()
    if unset:
        needs.append("owner risk limits unset: execution blocked")
        missing.extend(f"limits.{name}" for name in unset)

    # 3. Quote gate: present, fresh, real-time.
    q = plan.quote
    if q is None:
        needs.append("no timestamped quote supplied")
        missing.append("quote")
    else:
        if q.delayed:
            needs.append(f"quote from {q.source} is delayed")
        if limits.max_quote_age_seconds is not None:
            age = (now - q.timestamp).total_seconds()
            if age < 0:
                needs.append("quote timestamp is in the future")
            elif age > limits.max_quote_age_seconds:
                needs.append(f"quote is stale: {age:.0f}s old, limit {limits.max_quote_age_seconds:.0f}s")
        if limits.max_spread_bps is not None and q.spread_bps > limits.max_spread_bps:
            block.append(f"spread {q.spread_bps:.1f} bps exceeds limit {limits.max_spread_bps:.1f}")

    # 4. Loss and drawdown stops (only evaluable when the limit is set).
    if limits.daily_loss_limit is not None and -account.daily_pnl >= limits.daily_loss_limit:
        halt.append("daily loss limit reached")
    if limits.weekly_loss_limit is not None and -account.weekly_pnl >= limits.weekly_loss_limit:
        halt.append("weekly loss limit reached")
    if limits.max_drawdown_fraction is not None and account.drawdown_fraction >= limits.max_drawdown_fraction:
        halt.append("drawdown stop reached")

    # 5. Required reasoning fields.
    if not plan.opposing_thesis.strip():
        block.append("opposing thesis missing")
    if not plan.invalidation.strip():
        block.append("invalidation condition missing")

    # 6. Default-prohibited structures and leverage.
    if plan.uncovered_short_option:
        block.append("uncovered short options are prohibited in the default configuration")
    if plan.asset_class is AssetClass.CRYPTO_PERP and plan.leverage > 1 and limits.max_leverage is None:
        block.append("leveraged crypto requires an explicit max_leverage")
    if limits.max_leverage is not None and plan.leverage > limits.max_leverage:
        block.append(f"leverage {plan.leverage}x exceeds limit {limits.max_leverage}x")

    # 7. Sizing: quantity = floor(risk_budget / unit_risk), then exposure caps.
    max_qty: Optional[float] = None
    unit_risk = abs(plan.entry - plan.stop) * plan.multiplier + plan.per_unit_costs
    if unit_risk <= 0:
        needs.append("unit risk is not positive; sizing inputs invalid")
    elif limits.risk_per_trade_fraction is not None:
        budget = account.equity * limits.risk_per_trade_fraction
        max_qty = float(math.floor(budget / unit_risk))
        if plan.quantity > max_qty:
            block.append(f"quantity {plan.quantity:g} exceeds risk-sized maximum {max_qty:g}")

    notional = plan.quantity * plan.entry * plan.multiplier
    if limits.max_position_fraction is not None:
        pos = account.instrument_exposure + notional
        if pos > account.equity * limits.max_position_fraction:
            block.append("position concentration limit exceeded")
    if limits.max_gross_exposure_fraction is not None:
        if account.gross_exposure + notional > account.equity * limits.max_gross_exposure_fraction:
            block.append("gross exposure limit exceeded")
    if limits.max_net_exposure_fraction is not None:
        signed = notional if plan.side.value == "LONG" else -notional
        if abs(account.net_exposure + signed) > account.equity * limits.max_net_exposure_fraction:
            block.append("net exposure limit exceeded")

    # 8. Resolve by severity.
    if halt:
        return GateResult(plan_id=plan.plan_id, decision=Decision.HALT, approved=False,
                          reasons=halt + needs + block, missing=missing, max_quantity=max_qty)
    if needs:
        return GateResult(plan_id=plan.plan_id, decision=Decision.NEEDS_DATA, approved=False,
                          reasons=needs + block, missing=missing, max_quantity=max_qty)
    if block:
        return GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                          reasons=block, max_quantity=max_qty)
    if plan.mode is Mode.PAPER:
        return GateResult(plan_id=plan.plan_id, decision=Decision.PAPER_CANDIDATE, approved=True,
                          reasons=["all gate checks passed (paper only)"], max_quantity=max_qty)
    return GateResult(plan_id=plan.plan_id, decision=Decision.ANALYZE, approved=False,
                      reasons=["checks passed; RESEARCH mode never approves execution"],
                      max_quantity=max_qty)
