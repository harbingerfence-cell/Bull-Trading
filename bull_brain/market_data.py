"""Market-data adapter: fetch a quote, check freshness, attach it to a plan.

Never invents a price. If the provider fails or returns nothing, the plan's
quote is cleared (a caller-supplied quote is not trusted past the adapter) so
the risk gate returns NEEDS_DATA. Structurally valid quotes are attached even
when stale or delayed, so the gate reports the specific reason; the adapter's
own verdict is returned in `QuoteCheck` and journaled by the caller if wanted.
No concrete vendor is wired in: implement `QuoteProvider` for the chosen feed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol

from .models import Quote, RiskLimits, TradePlan


class QuoteProvider(Protocol):
    def get_quote(self, instrument: str, venue: str) -> Optional[Quote]:
        """Return the latest quote, or None if unavailable. May raise."""


class QuoteStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    DELAYED = "DELAYED"
    FUTURE = "FUTURE"          # timestamp ahead of the clock
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"            # provider raised or returned an invalid quote
    UNCHECKED = "UNCHECKED"    # max_quote_age_seconds unset: freshness unknowable


@dataclass(frozen=True)
class QuoteCheck:
    status: QuoteStatus
    age_seconds: Optional[float] = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status is QuoteStatus.FRESH


class StaticQuoteProvider:
    """In-memory provider for tests and replay. Keyed by (instrument, venue)."""

    def __init__(self, quotes: Optional[dict[tuple[str, str], Quote]] = None):
        self.quotes = dict(quotes or {})

    def set(self, instrument: str, venue: str, quote: Quote) -> None:
        self.quotes[(instrument, venue)] = quote

    def get_quote(self, instrument: str, venue: str) -> Optional[Quote]:
        return self.quotes.get((instrument, venue))


class MarketDataAdapter:
    def __init__(self, provider: QuoteProvider, limits: RiskLimits):
        self.provider = provider
        self.limits = limits

    def check(self, quote: Quote, now: datetime) -> QuoteCheck:
        age = (now - quote.timestamp).total_seconds()
        if age < 0:
            return QuoteCheck(QuoteStatus.FUTURE, age, "quote timestamp is in the future")
        if quote.delayed:
            return QuoteCheck(QuoteStatus.DELAYED, age, f"quote from {quote.source} is delayed")
        limit = self.limits.max_quote_age_seconds
        if limit is None:
            return QuoteCheck(QuoteStatus.UNCHECKED, age, "max_quote_age_seconds unset")
        if age > limit:
            return QuoteCheck(QuoteStatus.STALE, age, f"{age:.0f}s old, limit {limit:.0f}s")
        return QuoteCheck(QuoteStatus.FRESH, age)

    def fetch(self, instrument: str, venue: str,
              now: Optional[datetime] = None) -> tuple[Optional[Quote], QuoteCheck]:
        now = now or datetime.now(timezone.utc)
        try:
            quote = self.provider.get_quote(instrument, venue)
        except Exception as exc:  # provider/network/validation failure
            return None, QuoteCheck(QuoteStatus.ERROR, None, f"{type(exc).__name__}: {exc}")
        if quote is None:
            return None, QuoteCheck(QuoteStatus.UNAVAILABLE, None, "provider returned no quote")
        return quote, self.check(quote, now)

    def attach(self, plan: TradePlan,
               now: Optional[datetime] = None) -> tuple[TradePlan, QuoteCheck]:
        """Return a copy of `plan` with its quote set from the provider."""
        quote, check = self.fetch(plan.instrument, plan.venue, now)
        return plan.model_copy(update={"quote": quote}), check
