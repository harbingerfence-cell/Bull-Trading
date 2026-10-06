"""Headline classification for news-driven events (frozen rules).

Benzinga headlines for analyst actions and earnings are formulaic, so simple
anchored patterns work. Only single-symbol articles are classified (multi-symbol
roundups carry no clean signal). Each category maps to a trade direction.
"""
from __future__ import annotations

import re
from typing import Optional

# (category, direction, regex) evaluated in order; first match wins.
RULES: list[tuple[str, str, re.Pattern]] = [(c, d, re.compile(p, re.I)) for c, d, p in [
    ("downgrade", "SHORT", r"\bDowngrades?\b"),
    ("upgrade", "LONG", r"\bUpgrades?\b"),
    ("initiate_sell", "SHORT", r"\bInitiates? Coverage\b.*\b(Sell|Underperform|Underweight|Negative|Reduce)\b"),
    ("initiate_buy", "LONG", r"\bInitiates? Coverage\b.*\b(Buy|Outperform|Overweight|Positive|Strong Buy)\b"),
    ("guidance_cut", "SHORT", r"\b(Lowers?|Cuts?|Reduces?)\b.*\b(Guidance|Outlook|Forecast)\b"),
    ("guidance_raise", "LONG", r"\b(Raises?|Boosts?|Lifts?|Increases?)\b.*\b(Guidance|Outlook|Forecast)\b"),
    ("eps_miss", "SHORT", r"\bEPS\b[^,]*\bMiss(es)?\b"),
    ("eps_beat", "LONG", r"\bEPS\b[^,]*\bBeats?\b"),
    ("pt_cut", "SHORT", r"\bLowers? (the )?Price Target\b"),
    ("pt_raise", "LONG", r"\bRaises? (the )?Price Target\b"),
    ("offering", "SHORT", r"\b(Public|Stock|Common Stock|Secondary) Offering\b|\bPrices .*Offering\b|\bConvertible (Senior )?Notes?\b"),
    ("buyback", "LONG", r"\b(Share|Stock) (Buyback|Repurchase)\b|\bBuyback\b|\bRepurchase Program\b"),
]]
CATEGORIES = [c for c, _, _ in RULES]


def classify(headline: str, symbols: list[str]) -> Optional[tuple[str, str]]:
    """Return (category, direction) for a single-symbol article, else None."""
    if len(symbols) != 1:
        return None
    for cat, direction, rx in RULES:
        if rx.search(headline):
            return cat, direction
    return None
