"""Alpaca Market Data quote provider (latest NBBO quotes).

Implements `QuoteProvider` for equities/ETFs, crypto and options using the
Alpaca data REST API. Credentials come from the environment
(ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY) or constructor arguments, never
from files in this repo. Read-only: no trading endpoints are used here.

Feed notes (verify current terms with Alpaca): the free equity feed is `iex`
(real-time but IEX-venue only, so spreads can look wider/thinner than the
consolidated market); `sip` is the consolidated feed and needs a subscription.
Crypto has no feed parameter. Options use the `indicative` or `opra` feed.
Any HTTP, parse, or zero-quote problem raises or returns None so the adapter
reports ERROR/UNAVAILABLE and the gate returns NEEDS_DATA.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from .models import Quote

DATA_URL = "https://data.alpaca.markets"
_OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_FRAC = re.compile(r"(\.\d{6})\d+")

# transport(url, headers, timeout) -> parsed JSON dict
Transport = Callable[[str, dict, float], dict]


class AlpacaError(Exception):
    pass


def _urllib_transport(url: str, headers: dict, timeout: float) -> dict:
    if not url.startswith(DATA_URL + "/"):
        raise AlpacaError("refusing to send credentials to a non-Alpaca host")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise AlpacaError(f"HTTP {exc.code} from Alpaca data API") from None
    except urllib.error.URLError as exc:
        raise AlpacaError(f"network error: {exc.reason}") from None


def parse_timestamp(raw: str) -> datetime:
    """RFC3339 with up to nanosecond precision -> aware UTC datetime."""
    ts = _FRAC.sub(r"\1", raw.replace("Z", "+00:00"))
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise AlpacaError("timestamp without timezone")
    return dt.astimezone(timezone.utc)


class AlpacaQuoteProvider:
    def __init__(self, key_id: Optional[str] = None, secret: Optional[str] = None,
                 equity_feed: str = "iex", option_feed: str = "indicative",
                 timeout: float = 5.0, transport: Optional[Transport] = None):
        self._key = key_id or os.environ.get("ALPACA_API_KEY_ID", "")
        self._secret = secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        if not self._key or not self._secret:
            raise AlpacaError("Alpaca credentials not set (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)")
        if equity_feed not in ("iex", "sip", "delayed_sip"):
            raise ValueError("equity_feed must be iex, sip, or delayed_sip")
        if option_feed not in ("indicative", "opra"):
            raise ValueError("option_feed must be indicative or opra")
        self.equity_feed, self.option_feed = equity_feed, option_feed
        self.timeout = timeout
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:  # never expose credentials
        return f"AlpacaQuoteProvider(equity_feed={self.equity_feed!r})"

    # -- QuoteProvider -------------------------------------------------
    def get_quote(self, instrument: str, venue: str) -> Optional[Quote]:
        if "/" in instrument:
            kind = "crypto"
            url = f"{DATA_URL}/v1beta3/crypto/us/latest/quotes?" + urllib.parse.urlencode({"symbols": instrument})
            source = "alpaca:crypto"
        elif _OCC.match(instrument):
            kind = "option"
            url = f"{DATA_URL}/v1beta1/options/quotes/latest?" + urllib.parse.urlencode(
                {"symbols": instrument, "feed": self.option_feed})
            source = f"alpaca:options:{self.option_feed}"
        else:
            kind = "stock"
            sym = urllib.parse.quote(instrument, safe="")
            url = f"{DATA_URL}/v2/stocks/{sym}/quotes/latest?" + urllib.parse.urlencode({"feed": self.equity_feed})
            source = f"alpaca:{self.equity_feed}"

        body = self._transport(url, {"APCA-API-KEY-ID": self._key,
                                     "APCA-API-SECRET-KEY": self._secret,
                                     "Accept": "application/json"}, self.timeout)
        raw = body.get("quote") if kind == "stock" else (body.get("quotes") or {}).get(instrument)
        if not raw:
            return None
        bid, ask = float(raw.get("bp") or 0), float(raw.get("ap") or 0)
        if bid <= 0 or ask <= 0:  # Alpaca reports 0 for a missing side
            return None
        return Quote(bid=bid, ask=ask, timestamp=parse_timestamp(raw["t"]), source=source,
                     delayed=self.equity_feed == "delayed_sip" and kind == "stock")
