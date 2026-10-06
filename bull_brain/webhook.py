"""TradingView alert receiver -> gated PAPER plan.

An alert is only a SIGNAL. This module never trusts the alert's price: it
fetches a fresh quote, sizes from owner limits and account equity, builds a
TradePlan and sends it through the risk gate and broker. TradingView cannot
send custom headers, so the shared secret travels in the JSON body and is
compared in constant time. Alerts must carry their own thesis, opposing
thesis and invalidation; missing ones are blocked by the gate, not defaulted.

Alert message template (TradingView alert dialog, "Message"):
{"secret":"<TV_WEBHOOK_SECRET>","alert_id":"{{strategy.order.id}}-{{timenow}}","symbol":"{{ticker}}",
 "side":"LONG","stop_pct":0.01,"time":"{{timenow}}","thesis":"...","opposing_thesis":"...",
 "invalidation":"...","confidence":"LOW"}
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .market_data import MarketDataAdapter
from .models import AssetClass, Confidence, Decision, Mode, RiskLimits, Side, TradePlan

log = logging.getLogger("bull_brain.webhook")
MAX_BODY = 8192
SYMBOL_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z])?$")


class AlertPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    secret: str
    alert_id: str = Field(min_length=1, max_length=120)
    symbol: str
    side: Side
    stop: Optional[float] = Field(default=None, gt=0)
    stop_pct: Optional[float] = Field(default=None, gt=0, lt=0.2)
    time: Optional[datetime] = None
    thesis: str = ""
    opposing_thesis: str = ""
    invalidation: str = ""
    confidence: Confidence = Confidence.LOW

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        v = v.strip().upper().split(":")[-1]  # NASDAQ:AAPL -> AAPL
        if not SYMBOL_RE.match(v):
            raise ValueError("invalid symbol")
        return v

    @model_validator(mode="after")
    def _one_stop(self) -> "AlertPayload":
        if (self.stop is None) == (self.stop_pct is None):
            raise ValueError("provide exactly one of stop or stop_pct")
        return self


class AlertHandler:
    def __init__(self, broker, adapter: MarketDataAdapter, limits: RiskLimits, secret: str,
                 allowed_symbols: set[str], max_alert_age_seconds: float = 90.0,
                 max_alerts_per_day: int = 10, venue: str = "ALPACA"):
        if len(secret) < 16:
            raise ValueError("webhook secret must be at least 16 characters")
        self.broker, self.adapter, self.limits = broker, adapter, limits
        self._secret = secret.encode()
        self.allowed = {s.upper() for s in allowed_symbols}
        self.max_age, self.max_per_day, self.venue = max_alert_age_seconds, max_alerts_per_day, venue
        self._seen: set[str] = set()
        self._day: Optional[str] = None
        self._count = 0
        self._lock = threading.Lock()

    def handle(self, body: bytes, now: Optional[datetime] = None) -> tuple[int, dict]:
        now = now or datetime.now(timezone.utc)
        if len(body) > MAX_BODY:
            return 413, {"error": "payload too large"}
        try:
            raw = json.loads(body)
            if not isinstance(raw, dict):
                raise ValueError
        except ValueError:
            return 400, {"error": "invalid JSON"}
        given = str(raw.get("secret", "")).encode()
        if not hmac.compare_digest(given, self._secret):
            log.warning("webhook: bad secret")
            return 401, {"error": "unauthorized"}
        try:
            a = AlertPayload(**raw)
        except ValidationError as exc:
            return 400, {"error": "invalid alert", "detail": [e["msg"] for e in exc.errors()]}

        with self._lock:  # serialise: one order decision at a time
            day = now.date().isoformat()
            if day != self._day:
                self._day, self._count = day, 0
            if a.alert_id in self._seen:
                return 200, {"status": "duplicate", "alert_id": a.alert_id}
            if a.symbol not in self.allowed:
                return 200, {"status": "rejected", "reasons": [f"{a.symbol} not in allowed symbols"]}
            if a.time is not None:
                t = a.time if a.time.tzinfo else a.time.replace(tzinfo=timezone.utc)
                age = (now - t).total_seconds()
                if age > self.max_age or age < -30:
                    return 200, {"status": "rejected", "reasons": [f"alert time off by {age:.0f}s (limit {self.max_age:.0f}s)"]}
            if self._count >= self.max_per_day:
                return 200, {"status": "rejected", "reasons": ["daily alert cap reached"]}
            self._seen.add(a.alert_id)
            self._count += 1
            return self._process(a, now)

    def _process(self, a: AlertPayload, now: datetime) -> tuple[int, dict]:
        quote, chk = self.adapter.fetch(a.symbol, self.venue, now)
        if quote is None or not chk.usable:
            return 200, {"status": "no_data", "reasons": [f"quote unusable: {chk.status.value} {chk.detail}"]}
        long = a.side is Side.LONG
        entry = quote.ask if long else quote.bid
        stop = a.stop if a.stop is not None else entry * (1 - a.stop_pct if long else 1 + a.stop_pct)
        if (stop >= entry) if long else (stop <= entry):
            return 400, {"error": "stop is on the wrong side of the live price"}
        acct = self.broker.account_state(a.symbol)
        rf = self.limits.risk_per_trade_fraction
        if rf is None:
            return 200, {"status": "no_data", "reasons": ["risk_per_trade_fraction unset: cannot size"]}
        qty = acct.equity * rf / abs(entry - stop)
        if self.limits.max_position_fraction is not None:
            qty = min(qty, acct.equity * self.limits.max_position_fraction / entry)
        qty = float(math.floor(qty))
        if qty < 1:
            return 200, {"status": "rejected", "reasons": ["size rounds to zero shares"]}
        plan = TradePlan(plan_id=f"tv-{re.sub(r'[^A-Za-z0-9_.-]', '_', a.alert_id)[:100]}", mode=Mode.PAPER,
                         instrument=a.symbol, asset_class=AssetClass.EQUITY, venue=self.venue, side=a.side,
                         horizon="intraday", entry=entry, stop=stop, quantity=qty, quote=quote,
                         thesis=a.thesis, opposing_thesis=a.opposing_thesis,
                         invalidation=a.invalidation, confidence=a.confidence)
        res = self.broker.submit(plan, now)
        return 200, {"status": "submitted" if res.approved else "blocked", "decision": res.decision.value,
                     "reasons": res.reasons, "plan_id": plan.plan_id, "quantity": qty}


def make_server(handler: AlertHandler, host: str = "0.0.0.0", port: int = 8080,
                path_token: str = "") -> ThreadingHTTPServer:
    route = f"/tv/{path_token}" if path_token else "/tv"

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != route:
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "payload too large"})
            code, out = handler.handle(self.rfile.read(n))
            self._send(code, out)

        def do_GET(self):
            self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not found"})

        def _send(self, code: int, obj: dict):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # keep request lines (and any path token) out of logs
            pass

    return ThreadingHTTPServer((host, port), H)


def main() -> None:
    """python -m bull_brain.webhook --limits limits.json --symbols SPY,QQQ,AAPL
    Env: TV_WEBHOOK_SECRET (>=16 chars), TV_PATH_TOKEN (optional), ALPACA_* keys, PORT."""
    import argparse
    from .alpaca_broker import AlpacaPaperBroker
    from .alpaca_data import AlpacaQuoteProvider
    from .journal import Journal
    ap = argparse.ArgumentParser()
    ap.add_argument("--limits", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--journal", default="webhook_journal.jsonl")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    limits = RiskLimits(**json.load(open(a.limits)))
    provider = AlpacaQuoteProvider()
    h = AlertHandler(AlpacaPaperBroker(limits, Journal(a.journal)), MarketDataAdapter(provider, limits), limits,
                     os.environ["TV_WEBHOOK_SECRET"], set(a.symbols.split(",")))
    srv = make_server(h, port=int(os.environ.get("PORT", "8080")), path_token=os.environ.get("TV_PATH_TOKEN", ""))
    log.info("webhook listening on %s", srv.server_address)
    srv.serve_forever()


if __name__ == "__main__":
    main()
