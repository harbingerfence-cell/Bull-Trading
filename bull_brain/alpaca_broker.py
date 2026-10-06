"""Alpaca PAPER broker adapter (bracket orders).

Host is pinned to paper-api.alpaca.markets; credentials come from the
environment. Flow per plan: gate (with live Alpaca account state) -> bracket
order (entry + take-profit + stop-loss) with client_order_id = plan_id.
If the POST outcome is uncertain (timeout/5xx), the order is looked up by
client_order_id and reconciled before any retry; it is never blindly re-sent.
Equities/ETFs only here (whole shares).

API shapes (endpoints, field names) are from memory of Alpaca's Trading API v2
and are verified only against fakes in tests; confirm on first live paper call.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .alpaca_data import AlpacaError
from .journal import Journal
from .models import (AccountState, AssetClass, Decision, GateResult, Mode, RiskLimits, Side, TradePlan)
from .risk_gate import evaluate

PAPER_URL = "https://paper-api.alpaca.markets"

# transport(method, url, headers, body_or_None, timeout) -> (status, parsed_json)
Transport = Callable[[str, str, dict, Optional[dict], float], tuple[int, Any]]


class UncertainOrder(AlpacaError):
    """POST outcome unknown (timeout / 5xx / network)."""


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[dict], timeout: float):
    if not url.startswith(PAPER_URL + "/"):
        raise AlpacaError("refusing to send credentials to a non-paper Alpaca host")
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers)
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if exc.fp else ""
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"message": raw[:200]}
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UncertainOrder(f"network error: {getattr(exc, 'reason', exc)}") from None


class AlpacaPaperBroker:
    def __init__(self, limits: RiskLimits, journal: Optional[Journal] = None,
                 key_id: Optional[str] = None, secret: Optional[str] = None,
                 timeout: float = 8.0, transport: Optional[Transport] = None):
        self._key = key_id or os.environ.get("ALPACA_API_KEY_ID", "")
        self._secret = secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        if not self._key or not self._secret:
            raise AlpacaError("Alpaca credentials not set (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)")
        self.limits = limits
        self.journal = journal or Journal()
        self.timeout = timeout
        self._transport = transport or _urllib_transport
        self.peak_equity: Optional[float] = None
        self.weekly_pnl = 0.0  # Alpaca has no weekly figure; caller/scheduler maintains it

    def __repr__(self) -> str:
        return "AlpacaPaperBroker(paper)"

    # -- http ----------------------------------------------------------
    def _call(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret,
                   "Accept": "application/json"}
        return self._transport(method, PAPER_URL + path, headers, body, self.timeout)

    def _get(self, path: str) -> Any:
        status, data = self._call("GET", path)
        if status != 200:
            raise AlpacaError(f"GET {path.split('?')[0]} -> HTTP {status}")
        return data

    # -- state ---------------------------------------------------------
    def account_state(self, instrument: Optional[str] = None) -> AccountState:
        acct = self._get("/v2/account")
        equity = float(acct["equity"])
        daily = equity - float(acct.get("last_equity", equity))
        self.peak_equity = max(self.peak_equity or equity, equity)
        dd = max(0.0, (self.peak_equity - equity) / self.peak_equity)
        positions = self._get("/v2/positions")
        vals = [(p["symbol"], float(p["market_value"])) for p in positions]  # signed
        return AccountState(equity=equity, daily_pnl=daily, weekly_pnl=self.weekly_pnl,
                            drawdown_fraction=min(dd, 1.0),
                            gross_exposure=sum(abs(v) for _, v in vals),
                            net_exposure=sum(v for _, v in vals),
                            instrument_exposure=sum(abs(v) for s, v in vals if s == instrument))

    # -- orders --------------------------------------------------------
    def _order_body(self, plan: TradePlan) -> dict:
        long = plan.side is Side.LONG
        risk = abs(plan.entry - plan.stop)
        target = plan.entry + 2 * risk if long else plan.entry - 2 * risk
        return {"symbol": plan.instrument, "qty": str(int(plan.quantity)),
                "side": "buy" if long else "sell", "type": "market", "time_in_force": "day",
                "order_class": "bracket", "client_order_id": plan.plan_id,
                "take_profit": {"limit_price": f"{target:.2f}"},
                "stop_loss": {"stop_price": f"{plan.stop:.2f}"}}

    def lookup(self, client_order_id: str) -> Optional[dict]:
        status, data = self._call("GET", "/v2/orders:by_client_order_id?" +
                                  urllib.parse.urlencode({"client_order_id": client_order_id}))
        if status == 404:
            return None
        if status != 200:
            raise AlpacaError(f"order lookup -> HTTP {status}")
        return data

    def submit(self, plan: TradePlan, now: Optional[datetime] = None,
               take_profit: Optional[float] = None) -> GateResult:
        now = now or datetime.now(timezone.utc)
        self.journal.append("plan", plan.plan_id, plan.model_dump(mode="json"), now)

        if plan.mode is not Mode.PAPER:
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["paper broker only accepts PAPER mode plans"])
        elif plan.asset_class not in (AssetClass.EQUITY, AssetClass.ETF) or plan.multiplier != 1:
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["this adapter supports equities/ETFs only"])
        elif plan.quantity != int(plan.quantity):
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["whole-share quantities only"])
        elif self.lookup(plan.plan_id) is not None:
            res = GateResult(plan_id=plan.plan_id, decision=Decision.NO_TRADE, approved=False,
                             reasons=["duplicate plan_id: order already exists at broker"])
        else:
            res = evaluate(plan, self.limits, self.account_state(plan.instrument), now)
        self.journal.append("gate", plan.plan_id, res.model_dump(mode="json"), now)
        if not res.approved:
            return res

        body = self._order_body(plan)
        if take_profit is not None:
            body["take_profit"] = {"limit_price": f"{take_profit:.2f}"}
        order = self._place(body, plan.plan_id, now)
        self.journal.append("fill", plan.plan_id,
                            {"order_id": order.get("id"), "status": order.get("status"),
                             "client_order_id": plan.plan_id, "submitted_qty": body["qty"]}, now)
        return res

    def _place(self, body: dict, client_id: str, now: datetime) -> dict:
        try:
            status, data = self._call("POST", "/v2/orders", body)
        except UncertainOrder:
            existing = self.lookup(client_id)  # reconcile; never blind-retry
            if existing is None:
                self.journal.append("reject", client_id, {"reason": "uncertain POST; no order found"}, now)
                raise
            return existing
        if status >= 500:
            existing = self.lookup(client_id)
            if existing is None:
                self.journal.append("reject", client_id, {"reason": f"HTTP {status}; no order found"}, now)
                raise UncertainOrder(f"HTTP {status} and no order found")
            return existing
        if status not in (200, 201):
            self.journal.append("reject", client_id, {"reason": f"HTTP {status}", "detail": data}, now)
            raise AlpacaError(f"order rejected: HTTP {status} {data.get('message', '')}")
        return data

    def cancel_all(self) -> None:
        """Kill switch: cancel open orders and flatten positions."""
        self._call("DELETE", "/v2/orders")
        self._call("DELETE", "/v2/positions")
