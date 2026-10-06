"""Discretionary research notes: record, convert to a gated plan, score, calibrate.

A note is a falsifiable call written BEFORE the session: sources with
timestamps, thesis, strongest opposing thesis, invalidation, and exact
entry/stop/target. It becomes a TradePlan only through the risk gate. After
the session it is scored mechanically (first touch of stop/target) so the
AI's judgment is measured instead of trusted. Notes and scores are journaled.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .journal import Journal
from .models import AssetClass, Confidence, Mode, Quote, Side, TradePlan
from .orb import Bar


class Source(BaseModel):
    title: str
    origin: str                 # e.g. benzinga, SEC filing, issuer release
    published_at: datetime      # publication time, not event time


class ResearchNote(BaseModel):
    note_id: str
    session: date
    created_at: datetime        # must precede the entry window it trades
    symbol: str
    side: Side
    catalyst: str
    sources: list[Source] = Field(default_factory=list)
    thesis: str
    opposing_thesis: str
    invalidation: str
    entry: float = Field(gt=0)  # reference price; fills at market after created_at
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    confidence: Confidence = Confidence.LOW
    horizon_end: Optional[datetime] = None  # default: last bar of the session

    @model_validator(mode="after")
    def _levels(self) -> "ResearchNote":
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if not (self.thesis.strip() and self.opposing_thesis.strip() and self.invalidation.strip()):
            raise ValueError("thesis, opposing_thesis and invalidation are required")
        long = self.side is Side.LONG
        if long and not (self.stop < self.entry < self.target):
            raise ValueError("long note requires stop < entry < target")
        if not long and not (self.target < self.entry < self.stop):
            raise ValueError("short note requires target < entry < stop")
        return self

    @property
    def planned_rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


def note_to_plan(note: ResearchNote, quantity: float, quote: Optional[Quote],
                 mode: Mode = Mode.PAPER) -> TradePlan:
    return TradePlan(plan_id=f"note-{note.note_id}", mode=mode, instrument=note.symbol,
                     asset_class=AssetClass.EQUITY, venue="ALPACA", side=note.side, horizon="intraday",
                     entry=note.entry, stop=note.stop, quantity=quantity, quote=quote,
                     thesis=note.thesis, opposing_thesis=note.opposing_thesis,
                     invalidation=note.invalidation, confidence=note.confidence)


def record_note(journal: Journal, note: ResearchNote) -> None:
    journal.append("note", note.note_id, note.model_dump(mode="json"), note.created_at)


def score_note(note: ResearchNote, bars: list[Bar], slippage_bps: float = 2.0) -> dict:
    """Enter at the first bar opening at/after created_at; exit on first touch
    (stop first when a bar spans both) or at horizon end. Result in R."""
    long = note.side is Side.LONG
    d = 1 if long else -1
    slip = slippage_bps / 1e4
    live = sorted((b for b in bars if b.ts >= note.created_at), key=lambda b: b.ts)
    if note.horizon_end:
        live = [b for b in live if b.ts <= note.horizon_end]
    if not live:
        return {"note_id": note.note_id, "outcome": "NO_DATA", "r": None}
    entry = live[0].open * (1 + d * slip)
    risk = abs(entry - note.stop)
    if (long and entry <= note.stop) or (not long and entry >= note.stop) or risk <= 0:
        return {"note_id": note.note_id, "outcome": "INVALID_ENTRY", "r": None}
    exit_px, outcome = live[-1].close * (1 - d * slip), "TIME"
    for b in live:
        hs = b.low <= note.stop if long else b.high >= note.stop
        ht = b.high >= note.target if long else b.low <= note.target
        if hs:
            exit_px, outcome = note.stop * (1 - d * slip), "STOP"
            break
        if ht:
            exit_px, outcome = note.target, "TARGET"
            break
    if outcome == "TIME" and note.horizon_end and live[-1].ts < note.horizon_end - timedelta(days=1):
        return {"note_id": note.note_id, "outcome": "OPEN", "r": None, "confidence": note.confidence.value}
    r = (exit_px - entry) * d / risk
    return {"note_id": note.note_id, "outcome": outcome, "r": r, "entry": entry, "exit": exit_px,
            "confidence": note.confidence.value}


def record_score(journal: Journal, note: ResearchNote, score: dict) -> None:
    journal.append("score", note.note_id, score)


def calibration(scores: list[dict]) -> dict:
    """Per-confidence-level results. HIGH should beat LOW if the model's
    confidence means anything; with few notes treat every number as noise."""
    groups: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        if s.get("r") is not None:
            groups[s["confidence"]].append(s["r"])
    out = {}
    for conf, rs in groups.items():
        out[conf] = {"n": len(rs), "avg_r": sum(rs) / len(rs), "win_rate": sum(r > 0 for r in rs) / len(rs)}
    return out
