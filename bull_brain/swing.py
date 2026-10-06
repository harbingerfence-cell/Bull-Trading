"""Daily swing scanner + paper executor for the three long chart patterns.

  scan   (after the close): detect bull_flag / cup_handle / inverse_head_shoulders
         on the last COMPLETED daily bar, write swing_queue.json, journal a
         falsifiable note per setup.
  open   (~09:31 ET next day): fresh quote -> validate vs stop/target -> risk
         size -> risk gate -> GTC bracket order on the Alpaca PAPER account.
  manage (daily): flatten positions at the 30-trading-day time stop.
  score  (weekly): score journaled notes on daily bars (first touch, in R).

Rules are the frozen ones from scripts/run_patterns.py; nothing is tuned.
A market-regime filter (SPY > 200d) was tested once and REJECTED.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .journal import Journal
from .market_data import MarketDataAdapter
from .models import AssetClass, Confidence, Mode, RiskLimits, Side, TradePlan
from .notes import ResearchNote, record_note, record_score, score_note
from .orb import ET, Bar
from .patterns import DETECTORS, EVENT_DETECTORS, HOLD_DAYS, Series

SWING_PATTERNS = ("bull_flag", "cup_handle", "inverse_head_shoulders", "event_day")
ALL_DETECTORS = {**DETECTORS, **EVENT_DETECTORS}
MIN_BARS = 200


@dataclass
class Setup:
    symbol: str
    pattern: str
    signal_date: str      # ISO date of the completed daily bar
    trigger_close: float
    stop: float
    target: float
    side: str = "LONG"

    @property
    def note_id(self) -> str:
        return f"swing-{self.pattern}-{self.symbol}-{self.signal_date}"

    @property
    def rr(self) -> float:
        return abs(self.target - self.trigger_close) / abs(self.trigger_close - self.stop)


def scan(bars_by_symbol: dict[str, list[Bar]], now: Optional[datetime] = None,
         patterns: tuple[str, ...] = SWING_PATTERNS) -> list[Setup]:
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    out: list[Setup] = []
    for sym, bars in sorted(bars_by_symbol.items()):
        bars = sorted(bars, key=lambda b: b.ts)
        if bars and bars[-1].ts.astimezone(timezone.utc).date() == now.date() and now.time() < time(16, 5):
            bars = bars[:-1]  # today's bar is still forming
        if len(bars) < MIN_BARS or (now.date() - bars[-1].ts.date()).days > 4:
            continue          # too little history, or stale data
        s = Series(bars)
        t = len(bars) - 1
        for name in patterns:
            sig = ALL_DETECTORS[name](s, t)
            if sig:
                out.append(Setup(sym, name, bars[t].ts.date().isoformat(), bars[t].close, sig.stop, sig.target,
                                 sig.side))
    order = {p: i for i, p in enumerate(patterns)}
    return sorted(out, key=lambda x: (order[x.pattern], x.symbol))


def save_queue(path: str | Path, asof: date, setups: list[Setup]) -> None:
    Path(path).write_text(json.dumps({"asof": asof.isoformat(), "setups": [asdict(s) for s in setups]}, indent=1))


def queue_is_fresh(asof: Optional[str], now: datetime, max_age_days: int = 4) -> bool:
    """A queue is usable only if scanned within the last few days (covers weekends/holidays)."""
    if not asof:
        return False
    age = (now.astimezone(ET).date() - date.fromisoformat(asof)).days
    return 0 <= age <= max_age_days


def load_queue(path: str | Path) -> tuple[Optional[str], list[Setup]]:
    p = Path(path)
    if not p.exists():
        return None, []
    d = json.loads(p.read_text())
    return d["asof"], [Setup(**s) for s in d["setups"]]


def setup_to_note(st: Setup) -> ResearchNote:
    d = date.fromisoformat(st.signal_date)
    created = datetime.combine(d, time(16, 0), tzinfo=ET)
    long = st.side == "LONG"
    return ResearchNote(
        note_id=st.note_id, session=d, created_at=created, symbol=st.symbol, side=Side.LONG if long else Side.SHORT,
        catalyst=f"{st.pattern} signal on the daily chart",
        thesis=f"{st.pattern} {st.side} signal on the close of {st.signal_date}; target {st.target:.2f}",
        opposing_thesis="false signal or reversal; historical results may reflect market drift or noise, not skill",
        invalidation=f"trades {'below' if long else 'above'} {st.stop:.2f}", entry=st.trigger_close, stop=st.stop,
        target=st.target,
        confidence=Confidence.LOW, horizon_end=created + timedelta(days=45))


def record_setups(journal: Journal, setups: list[Setup]) -> None:
    have = {e["plan_id"] for e in journal.entries(event="note")}
    for st in setups:
        if st.note_id not in have:
            record_note(journal, setup_to_note(st))


def open_setups(setups: list[Setup], broker, adapter: MarketDataAdapter, limits: RiskLimits,
                now: Optional[datetime] = None, max_new: int = 3, max_open: int = 8,
                venue: str = "ALPACA") -> list[dict]:
    now = now or datetime.now(timezone.utc)
    results, new = [], 0
    held = set(broker.position_symbols())
    for st in setups:
        r = {"setup": st.note_id}
        if new >= max_new or len(held) >= max_open:
            results.append({**r, "status": "skipped", "reason": "daily/open position cap"})
            continue
        if st.symbol in held:
            results.append({**r, "status": "skipped", "reason": "already holding symbol"})
            continue
        quote, chk = adapter.fetch(st.symbol, venue, now)
        if quote is None or not chk.usable:
            results.append({**r, "status": "no_data", "reason": f"quote unusable: {chk.status.value} {chk.detail}"})
            continue
        long = st.side == "LONG"
        entry = quote.ask if long else quote.bid
        ok = (st.stop < entry < st.target) if long else (st.target < entry < st.stop)
        if not ok or abs(st.target - entry) < abs(entry - st.stop):
            results.append({**r, "status": "skipped", "reason": f"live price {entry:.2f} invalid vs stop/target"})
            continue
        if limits.risk_per_trade_fraction is None:
            results.append({**r, "status": "no_data", "reason": "risk_per_trade_fraction unset"})
            continue
        eq = broker.account_state(st.symbol).equity
        qty = eq * limits.risk_per_trade_fraction / abs(entry - st.stop)
        if limits.max_position_fraction is not None:
            qty = min(qty, eq * limits.max_position_fraction / entry)
        qty = float(math.floor(qty))
        if qty < 1:
            results.append({**r, "status": "skipped", "reason": "size rounds to zero"})
            continue
        n = setup_to_note(st)
        plan = TradePlan(plan_id=st.note_id, mode=Mode.PAPER, instrument=st.symbol, asset_class=AssetClass.EQUITY,
                         venue=venue, side=Side.LONG if long else Side.SHORT, horizon="swing", entry=entry,
                         stop=st.stop, quantity=qty, quote=quote, thesis=n.thesis,
                         opposing_thesis=n.opposing_thesis, invalidation=n.invalidation, confidence=Confidence.LOW)
        try:
            res = broker.submit(plan, now, take_profit=st.target, time_in_force="gtc")
        except Exception as exc:  # e.g. symbol not shortable / broker rejection: keep going
            results.append({**r, "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if res.approved:
            new += 1
            held.add(st.symbol)
        results.append({**r, "status": "submitted" if res.approved else "blocked",
                        "decision": res.decision.value, "reasons": res.reasons, "quantity": qty})
    return results


def _weekdays_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def manage(broker, journal: Journal, now: Optional[datetime] = None, max_hold_days: int = 30) -> list[str]:
    """Flatten swing positions at the time stop. Returns plan_ids flattened."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    exited = {e["plan_id"] for e in journal.entries(event="exit")}
    plans = {e["plan_id"]: e["data"] for e in journal.entries(event="plan")}
    out = []
    for f in journal.entries(event="fill"):
        pid = f["plan_id"]
        if not pid.startswith("swing-") or pid in exited:
            continue
        filled = datetime.fromisoformat(f["at"]).astimezone(ET).date()
        sym = plans[pid]["instrument"]
        limit_days = HOLD_DAYS.get(pid.split("-")[1], max_hold_days)
        if _weekdays_between(filled, now.date()) >= limit_days and broker.has_position(sym):
            broker.flatten(sym)
            journal.append("exit", pid, {"reason": f"time_stop_{limit_days}d"}, now)
            out.append(pid)
    return out


def score_notes(journal: Journal, bars_fn: Callable[[str, datetime, datetime], list[Bar]],
                now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    scored = {e["plan_id"] for e in journal.entries(event="score")}
    out = []
    for e in journal.entries(event="note"):
        if e["plan_id"] in scored:
            continue
        note = ResearchNote(**e["data"])
        bars = bars_fn(note.symbol, note.created_at - timedelta(days=3), now)
        sc = score_note(note, bars)
        if sc["outcome"] in ("OPEN", "NO_DATA"):
            continue
        record_score(journal, note, sc)
        out.append(sc)
    return out


def main() -> None:
    """python -m bull_brain.swing {scan|open|manage|score} [--symbols A,B] [--limits limits.paper.json]"""
    import argparse
    from .alpaca_broker import AlpacaPaperBroker
    from .alpaca_data import AlpacaQuoteProvider
    from .bars import fetch_alpaca_bars
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "open", "manage", "score"])
    ap.add_argument("--symbols", default="SPY,QQQ,IWM,DIA,XLF,XLE,XLK,XLV,XLY,XLP,XLI,XLU,XLB,AAPL,MSFT,NVDA,TSLA,AMD,META,AMZN,"
                                         "GOOGL,NFLX,AVGO,PLTR,MU,JPM,BAC,WMT,XOM,CVX,UNH,HD,V,MA,COST,KO,PEP,DIS,INTC,CSCO,"
                                         "ORCL,CRM,ADBE,QCOM,TXN,GS,CAT")
    ap.add_argument("--limits", default="limits.paper.json")
    ap.add_argument("--journal", default="swing_journal.jsonl")
    ap.add_argument("--queue", default="swing_queue.json")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    provider = AlpacaQuoteProvider(timeout=60)
    journal = Journal(a.journal)

    def bars_fn(sym, start, end):
        return fetch_alpaca_bars(provider, sym, start, end, timeframe="1Day", feed="sip")

    if a.cmd == "scan":
        data = {s: bars_fn(s, now - timedelta(days=700), now - timedelta(minutes=20)) for s in a.symbols.split(",")}
        setups = scan(data, now)
        save_queue(a.queue, now.astimezone(ET).date(), setups)
        record_setups(journal, setups)
        print(f"{len(setups)} setups:", [f"{s.symbol}:{s.pattern}" for s in setups])
        return
    limits = RiskLimits(**json.load(open(a.limits)))
    broker = AlpacaPaperBroker(limits, journal)
    if a.cmd == "open":
        asof, setups = load_queue(a.queue)
        print("queue as of", asof, "-", len(setups), "setups")
        if not setups:
            print("nothing queued")
        elif not queue_is_fresh(asof, now):
            print("queue is stale; skipping")
        elif not broker.is_market_open():
            print("market closed; skipping (queue kept)")
        else:
            for r in open_setups(setups, broker, MarketDataAdapter(provider, limits), limits, now):
                print(r)
            save_queue(a.queue, date.fromisoformat(asof), [])  # consumed: never re-run
    elif a.cmd == "manage":
        print("flattened:", manage(broker, journal, now))
    else:
        print("scored:", score_notes(journal, bars_fn, now))


if __name__ == "__main__":
    main()
