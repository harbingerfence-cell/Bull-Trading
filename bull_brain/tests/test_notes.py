from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from bull_brain.journal import Journal
from bull_brain.models import Confidence, Decision, Side
from bull_brain.notes import (ResearchNote, Source, calibration, note_to_plan, record_note,
                              record_score, score_note)
from bull_brain.orb import ET, Bar
from bull_brain.paper_broker import PaperBroker
from bull_brain.tests.test_acceptance import NOW, full_limits, quote
from bull_brain.tests.test_orb import mk

T0 = datetime(2026, 10, 7, 9, 36, tzinfo=ET)


def note(**o):
    base = dict(note_id="n1", session=T0.date(), created_at=T0, symbol="XYZ", side=Side.LONG,
                catalyst="guidance raise", sources=[Source(title="XYZ raises guidance", origin="issuer release",
                                                            published_at=T0 - timedelta(hours=2))],
                thesis="gap holds above prior high", opposing_thesis="gap fill on weak breadth",
                invalidation="loses 98", entry=100.0, stop=98.0, target=104.0, confidence=Confidence.MEDIUM)
    base.update(o)
    return ResearchNote(**base)


def test_validation_rejects_bad_levels_and_missing_reasoning():
    with pytest.raises(ValidationError):
        note(stop=101.0)
    with pytest.raises(ValidationError):
        note(side=Side.SHORT)           # long levels on a short
    with pytest.raises(ValidationError):
        note(opposing_thesis=" ")
    assert note().planned_rr == 2.0


def test_note_becomes_plan_only_through_gate():
    plan = note_to_plan(note(), 40, quote())
    r = PaperBroker(full_limits(), 10_000).submit(plan, NOW)
    assert r.decision is Decision.PAPER_CANDIDATE and plan.plan_id == "note-n1"


def bars_after(path):
    return mk(path, start=T0)


def test_score_target_stop_time_and_invalid_entry():
    win = score_note(note(), bars_after([100.0, 101.0, 102.5, 104.5, 104.5]), slippage_bps=0)
    assert win["outcome"] == "TARGET" and win["r"] == pytest.approx(2.0)
    loss = score_note(note(), bars_after([100.0, 99.0, 97.5, 97.0]), slippage_bps=0)
    assert loss["outcome"] == "STOP" and loss["r"] == pytest.approx(-1.0)
    t = score_note(note(), bars_after([100.0, 100.5, 101.0]), slippage_bps=0)
    assert t["outcome"] == "TIME" and 0 < t["r"] < 1
    assert score_note(note(), bars_after([97.0, 97.0]), slippage_bps=0)["outcome"] == "INVALID_ENTRY"
    assert score_note(note(), [], 0)["outcome"] == "NO_DATA"


def test_calibration_and_journal_chain(tmp_path):
    j = Journal(tmp_path / "n.jsonl")
    n = note()
    record_note(j, n)
    s = score_note(n, bars_after([100.0, 101.0, 102.5, 104.5]), 0)
    record_score(j, n, s)
    Journal(tmp_path / "n.jsonl")  # reload verifies chain
    cal = calibration([s, {"r": -1.0, "confidence": "MEDIUM"}, {"r": None, "confidence": "LOW"}])
    assert cal["MEDIUM"]["n"] == 2 and "LOW" not in cal
