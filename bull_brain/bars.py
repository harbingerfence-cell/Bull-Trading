"""Historical bar loaders: CSV and Alpaca (1-min bars)."""
from __future__ import annotations

import csv
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .alpaca_data import DATA_URL, AlpacaQuoteProvider, parse_timestamp
from .orb import Bar


def load_csv(path: str | Path) -> list[Bar]:
    """CSV columns: timestamp (ISO, tz-aware), open, high, low, close[, volume].
    Header names are case-insensitive; 't','o','h','l','c','v' also accepted."""
    alias = {"t": "timestamp", "time": "timestamp", "datetime": "timestamp", "o": "open",
             "h": "high", "l": "low", "c": "close", "v": "volume"}
    bars: list[Bar] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            r = {alias.get(k.strip().lower(), k.strip().lower()): v for k, v in row.items()}
            bars.append(Bar(parse_timestamp(r["timestamp"].strip()), float(r["open"]), float(r["high"]),
                            float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
    return sorted(bars, key=lambda b: b.ts)


def fetch_alpaca_bars(provider: AlpacaQuoteProvider, symbol: str, start: datetime, end: datetime,
                      timeframe: str = "1Min", feed: Optional[str] = None) -> list[Bar]:
    """Page through Alpaca stock bars. Reuses the provider's credentials/transport."""
    headers = {"APCA-API-KEY-ID": provider._key, "APCA-API-SECRET-KEY": provider._secret,
               "Accept": "application/json"}
    bars: list[Bar] = []
    token: Optional[str] = None
    while True:
        q = {"timeframe": timeframe, "start": start.astimezone(timezone.utc).isoformat(),
             "end": end.astimezone(timezone.utc).isoformat(), "limit": 10000,
             "feed": feed or provider.equity_feed, "adjustment": "split", "sort": "asc"}
        if token:
            q["page_token"] = token
        url = f"{DATA_URL}/v2/stocks/{urllib.parse.quote(symbol, safe='')}/bars?" + urllib.parse.urlencode(q)
        body = provider._transport(url, headers, provider.timeout)
        for r in body.get("bars") or []:
            bars.append(Bar(parse_timestamp(r["t"]), r["o"], r["h"], r["l"], r["c"], r.get("v", 0)))
        token = body.get("next_page_token")
        if not token:
            return bars
