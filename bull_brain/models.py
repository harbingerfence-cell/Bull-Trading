"""Typed contracts for Bull Trading Brain.

Mirrors brain/README.md: decision states, default-UNSET risk limits, and the
trade-plan output. The model's text is never an order; this schema is what the
deterministic risk gate validates.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Mode(str, Enum):
    RESEARCH = "RESEARCH"  # default; never approves execution
    PAPER = "PAPER"        # simulation only
    LIVE = "LIVE"          # disabled in this module


class Decision(str, Enum):
    ANALYZE = "ANALYZE"
    WATCH = "WATCH"
    PAPER_CANDIDATE = "PAPER_CANDIDATE"
    NO_TRADE = "NO_TRADE"
    NEEDS_DATA = "NEEDS_DATA"
    HALT = "HALT"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERP = "CRYPTO_PERP"
    OPTION = "OPTION"


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Confidence(str, Enum):
    """Qualitative only. Probabilities require a named, validated model."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskLimits(BaseModel):
    """Owner-approved limits. Every field defaults to None (UNSET).

    Any unset field blocks execution (README: 'unset required limits block
    execution').
    """
    model_config = ConfigDict(extra="forbid")

    risk_per_trade_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    daily_loss_limit: Optional[float] = Field(default=None, gt=0)   # currency
    weekly_loss_limit: Optional[float] = Field(default=None, gt=0)  # currency
    max_drawdown_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    max_gross_exposure_fraction: Optional[float] = Field(default=None, gt=0)
    max_net_exposure_fraction: Optional[float] = Field(default=None, gt=0)
    max_leverage: Optional[float] = Field(default=None, ge=1)
    max_position_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    max_spread_bps: Optional[float] = Field(default=None, gt=0)
    max_quote_age_seconds: Optional[float] = Field(default=None, gt=0)

    def unset_fields(self) -> list[str]:
        return [name for name in type(self).model_fields if getattr(self, name) is None]


class Quote(BaseModel):
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    timestamp: datetime
    source: str = Field(min_length=1)
    delayed: bool = False

    @model_validator(mode="after")
    def _checks(self) -> "Quote":
        if self.timestamp.tzinfo is None:
            raise ValueError("quote timestamp must be timezone-aware")
        if self.ask < self.bid:
            raise ValueError("crossed quote: ask < bid")
        return self

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4


class AccountState(BaseModel):
    equity: float = Field(gt=0)
    daily_pnl: float = 0.0           # negative = loss
    weekly_pnl: float = 0.0
    drawdown_fraction: float = Field(default=0.0, ge=0, le=1)
    gross_exposure: float = Field(default=0.0, ge=0)  # currency notional
    net_exposure: float = 0.0                         # signed currency notional
    instrument_exposure: float = Field(default=0.0, ge=0)  # existing, this instrument


class TradePlan(BaseModel):
    plan_id: str
    mode: Mode = Mode.RESEARCH
    instrument: str
    asset_class: AssetClass
    venue: str
    side: Side
    horizon: str
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    quantity: float = Field(gt=0)
    multiplier: float = Field(default=1.0, gt=0)  # e.g. 100 for standard US equity options
    per_unit_costs: float = Field(default=0.0, ge=0)
    leverage: float = Field(default=1.0, ge=1)
    uncovered_short_option: bool = False
    quote: Optional[Quote] = None
    thesis: str = ""
    opposing_thesis: str = ""
    invalidation: str = ""
    confidence: Confidence = Confidence.LOW

    @model_validator(mode="after")
    def _stop_side(self) -> "TradePlan":
        if self.side is Side.LONG and self.stop >= self.entry:
            raise ValueError("long plan requires stop below entry")
        if self.side is Side.SHORT and self.stop <= self.entry:
            raise ValueError("short plan requires stop above entry")
        return self


class GateResult(BaseModel):
    plan_id: str
    decision: Decision
    approved: bool                    # True only for PAPER_CANDIDATE in PAPER mode
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    max_quantity: Optional[float] = None
