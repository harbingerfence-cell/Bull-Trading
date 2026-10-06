"""README 'Acceptance scenarios' as executable tests.

Scenarios that depend on model behavior (e.g. ignoring an injected page
instruction) can't be tested at the gate; they belong in the prompt-eval
harness. This file covers everything the deterministic gate can enforce.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bull_brain.models import (
    AccountState, AssetClass, Confidence, Decision, Mode, Quote,
    RiskLimits, Side, TradePlan,
)
from bull_brain.risk_gate import evaluate

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


def full_limits(**over) -> RiskLimits:
    base = dict(
        risk_per_trade_fraction=0.01, daily_loss_limit=500, weekly_loss_limit=1500,
        max_drawdown_fraction=0.10, max_gross_exposure_fraction=1.0,
        max_net_exposure_fraction=1.0, max_leverage=1.0, max_position_fraction=0.50,
        max_spread_bps=20, max_quote_age_seconds=15,
    )
    base.update(over)
    return RiskLimits(**base)


def quote(age_s=2, **over) -> Quote:
    base = dict(bid=99.95, ask=100.05, timestamp=NOW - timedelta(seconds=age_s), source="test-feed")
    base.update(over)
    return Quote(**base)


def plan(**over) -> TradePlan:
    base = dict(
        plan_id="t1", mode=Mode.PAPER, instrument="XYZ", asset_class=AssetClass.EQUITY,
        venue="TEST", side=Side.LONG, horizon="swing", entry=100.0, stop=98.0,
        quantity=40, quote=quote(), thesis="trend continuation",
        opposing_thesis="range-bound, failed breakout", invalidation="close below 98",
        confidence=Confidence.HIGH,
    )
    base.update(over)
    return TradePlan(**base)


ACCT = AccountState(equity=10_000)


def test_clean_paper_plan_passes():
    r = evaluate(plan(), full_limits(), ACCT, NOW)
    assert r.decision is Decision.PAPER_CANDIDATE and r.approved
    assert r.max_quantity == 50  # 100 budget / 2 unit risk


def test_research_mode_never_approves():
    r = evaluate(plan(mode=Mode.RESEARCH), full_limits(), ACCT, NOW)
    assert r.decision is Decision.ANALYZE and not r.approved


def test_missing_quote_needs_data():
    r = evaluate(plan(quote=None), full_limits(), ACCT, NOW)
    assert r.decision is Decision.NEEDS_DATA and "quote" in r.missing and not r.approved


def test_stale_quote_needs_data():
    r = evaluate(plan(quote=quote(age_s=60)), full_limits(), ACCT, NOW)
    assert r.decision is Decision.NEEDS_DATA and any("stale" in x for x in r.reasons)


def test_delayed_quote_needs_data():
    r = evaluate(plan(quote=quote(delayed=True)), full_limits(), ACCT, NOW)
    assert r.decision is Decision.NEEDS_DATA


def test_unset_limits_block_execution():
    r = evaluate(plan(), RiskLimits(), ACCT, NOW)
    assert r.decision is Decision.NEEDS_DATA and not r.approved
    assert "limits.risk_per_trade_fraction" in r.missing


def test_one_unset_limit_is_enough_to_block():
    r = evaluate(plan(), full_limits(max_leverage=None), ACCT, NOW)
    assert r.decision is Decision.NEEDS_DATA and r.missing == ["limits.max_leverage"]


def test_high_confidence_but_failed_risk_check_is_no_trade():
    r = evaluate(plan(quantity=500), full_limits(), ACCT, NOW)
    assert r.decision is Decision.NO_TRADE and not r.approved


def test_live_mode_halts():
    r = evaluate(plan(mode=Mode.LIVE), full_limits(), ACCT, NOW)
    assert r.decision is Decision.HALT and not r.approved


def test_daily_loss_limit_halts():
    r = evaluate(plan(), full_limits(), AccountState(equity=10_000, daily_pnl=-500), NOW)
    assert r.decision is Decision.HALT


def test_drawdown_halts():
    r = evaluate(plan(), full_limits(), AccountState(equity=10_000, drawdown_fraction=0.12), NOW)
    assert r.decision is Decision.HALT


def test_uncovered_short_option_prohibited():
    p = plan(asset_class=AssetClass.OPTION, side=Side.SHORT, entry=2.0, stop=3.0,
             quantity=1, multiplier=100, uncovered_short_option=True)
    r = evaluate(p, full_limits(), ACCT, NOW)
    assert r.decision is Decision.NO_TRADE


def test_option_multiplier_counts_in_sizing():
    # 1 contract risks (3.00-2.00)*100 = $100 = exactly the 1% budget
    ok = plan(asset_class=AssetClass.OPTION, entry=3.0, stop=2.0, quantity=1,
              multiplier=100, quote=quote(bid=2.95, ask=3.05))
    wide = full_limits(max_spread_bps=500)  # options spreads are wide in bps terms
    assert evaluate(ok, wide, ACCT, NOW).decision is Decision.PAPER_CANDIDATE
    too_big = ok.model_copy(update={"quantity": 2})
    assert evaluate(too_big, wide, ACCT, NOW).decision is Decision.NO_TRADE


def test_missing_opposing_thesis_or_invalidation_blocks():
    assert evaluate(plan(opposing_thesis=""), full_limits(), ACCT, NOW).decision is Decision.NO_TRADE
    assert evaluate(plan(invalidation=" "), full_limits(), ACCT, NOW).decision is Decision.NO_TRADE


def test_wide_spread_blocks():
    r = evaluate(plan(quote=quote(bid=99.0, ask=101.0)), full_limits(), ACCT, NOW)
    assert r.decision is Decision.NO_TRADE


def test_concentration_and_gross_exposure_block():
    # 40 * 100 = 4000 notional = 40% of equity: fine under a 50% cap, blocked at 30%
    assert evaluate(plan(), full_limits(), ACCT, NOW).approved
    r = evaluate(plan(), full_limits(max_position_fraction=0.30), ACCT, NOW)
    assert r.decision is Decision.NO_TRADE and any("concentration" in x for x in r.reasons)
    r2 = evaluate(plan(), full_limits(), AccountState(equity=10_000, gross_exposure=9_000), NOW)
    assert r2.decision is Decision.NO_TRADE


def test_leverage_over_limit_blocks():
    r = evaluate(plan(leverage=5), full_limits(max_leverage=2), ACCT, NOW)
    assert r.decision is Decision.NO_TRADE


def test_invalid_stop_side_rejected_at_schema():
    with pytest.raises(ValidationError):
        plan(side=Side.LONG, stop=101.0)
    with pytest.raises(ValidationError):
        plan(side=Side.SHORT, stop=99.0)


def test_naive_timestamp_and_crossed_quote_rejected():
    with pytest.raises(ValidationError):
        Quote(bid=1, ask=1.1, timestamp=datetime(2026, 10, 6), source="x")
    with pytest.raises(ValidationError):
        Quote(bid=2, ask=1, timestamp=NOW, source="x")


def test_halt_outranks_needs_data():
    r = evaluate(plan(quote=None, mode=Mode.LIVE), full_limits(), ACCT, NOW)
    assert r.decision is Decision.HALT
