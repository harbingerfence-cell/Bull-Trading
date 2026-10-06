import json
from datetime import timedelta

import pytest

from bull_brain.journal import Journal, JournalError
from bull_brain.models import Decision, Mode
from bull_brain.paper_broker import PaperBroker
from bull_brain.tests.test_acceptance import NOW, full_limits, plan, quote


def broker(**kw):
    return PaperBroker(full_limits(**kw), equity=10_000)


def test_approved_plan_fills_at_ask_plus_slippage():
    b = broker()
    r = b.submit(plan(), NOW)
    assert r.approved
    pos = b.positions["t1"]
    assert pos.fill_price == pytest.approx(100.05 * 1.0001)


def test_gate_failure_does_not_fill():
    b = broker()
    r = b.submit(plan(opposing_thesis=""), NOW)
    assert r.decision is Decision.NO_TRADE and not b.positions


def test_duplicate_plan_id_rejected():
    b = broker()
    b.submit(plan(), NOW)
    r = b.submit(plan(), NOW)
    assert not r.approved and "duplicate" in r.reasons[0]
    assert len(b.positions) == 1


def test_non_paper_mode_rejected():
    b = broker()
    assert not b.submit(plan(mode=Mode.RESEARCH), NOW).approved
    assert not b.submit(plan(plan_id="t2", mode=Mode.LIVE), NOW).approved
    assert not b.positions


def test_close_updates_equity_and_loss_limit_halts():
    b = broker(daily_loss_limit=50)
    b.submit(plan(), NOW)
    t = b.close("t1", 98.0, "stop", NOW)
    assert t.pnl < -50 and b.equity < 10_000
    r = b.submit(plan(plan_id="t3", quote=quote()), NOW)
    assert r.decision is Decision.HALT


def test_exposure_tracks_open_positions():
    b = broker(max_position_fraction=0.5)
    b.submit(plan(), NOW)  # ~$4,000 notional
    r = b.submit(plan(plan_id="t2"), NOW)  # same instrument -> 80% > 50%
    assert not r.approved and "concentration" in " ".join(r.reasons)


def test_short_pnl_and_close_unknown():
    b = broker()
    b.submit(plan(side=plan().side.__class__.SHORT, entry=100.0, stop=102.0, quantity=40), NOW)
    t = b.close("t1", 99.0, "target", NOW)
    assert t.pnl > 0
    with pytest.raises(KeyError):
        b.close("nope", 1.0, "x")


def test_journal_records_lifecycle_and_detects_tampering(tmp_path):
    path = tmp_path / "j.jsonl"
    b = PaperBroker(full_limits(), 10_000, Journal(path))
    b.submit(plan(), NOW)
    b.close("t1", 104.0, "target", NOW + timedelta(hours=1))
    b.review("t1", "followed plan", True, NOW + timedelta(hours=2))
    assert [e["event"] for e in b.journal.entries("t1")] == ["plan", "gate", "fill", "exit", "review"]
    Journal(path)  # reload verifies the chain
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[3])
    tampered["data"]["pnl"] = 9999
    lines[3] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(JournalError):
        Journal(path)
