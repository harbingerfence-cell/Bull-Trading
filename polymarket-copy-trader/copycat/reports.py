"""Daily report, weekly trader review, and the 14-day paper results report."""
import csv
import os
import time
from collections import Counter

from . import api, config as C
from .engine import fmt_ct
from .markets import classify


def daily(engine):
    s = engine.s
    p = s["period"]
    eq = engine.equity()
    start = p["start_equity"] if p["start_equity"] is not None else eq
    lines = ["COPYCAT daily (PAPER) %s" % fmt_ct()[:10],
             "P&L since last report: %+.2f | total: %+.2f | equity %.2f (peak %.2f)" % (
                 eq - start, eq - s["bankroll_start"], eq, s["peak_equity"])]
    pos = s["positions"]
    lines.append("Open: %d positions, $%.2f at cost, unrealized %+.2f" % (len(pos), engine.open_cost(), engine.unrealized()))
    for x in pos[:10]:
        lines.append("  %s | %s %s @ %.3f, now %.3f, $%.0f" % (x["trader"], x["title"][:50], x["outcome"],
                                                              x["entry_price"], x["mark"], x["cost"]))
    lines.append("Copied: %d" % len(p["copied"]))
    for c in p["copied"][:10]:
        lines.append("  %s | %s %s @ %.3f $%.0f%s" % (c["trader"], c["market"][:50], c["side"], c["price"], c["usd"],
                                                     " (1.5x)" if c["confirmed"] else ""))
    if p["closed"]:
        lines.append("Closed: %d, %+.2f" % (len(p["closed"]), sum(c["pnl"] for c in p["closed"])))
    reasons = Counter(k["reason"].split(" (")[0] if k["reason"].startswith("category") else k["reason"]
                      for k in p["skipped"])
    lines.append("Skipped: %d" % len(p["skipped"]) + ("" if not reasons else " | " + ", ".join(
        "%s x%d" % (r, n) for r, n in reasons.most_common(6))))
    lines.append("Limit hits: " + (", ".join(p["limit_hits"]) if p["limit_hits"] else "none"))
    state = "PAUSED (drawdown)" if s["dd_paused"] else "halted today (daily loss)" if s["daily_halt"] else "running"
    lines.append("Status: " + state)
    for n in p["notes"]:
        lines.append("Note: " + n)
    return "\n".join(lines)


def _stats(closed, now):
    def window(days):
        rows = [c for c in closed if days is None or c["ts"] >= now - days * 86400]
        pnl = sum(c["pnl"] for c in rows)
        wr = (sum(1 for c in rows if c["pnl"] > 0) / len(rows)) if rows else None
        return pnl, wr, len(rows)
    d7, d30, all_ = window(7), window(30), window(None)
    nonsports = [c for c in closed if classify([], c["title"]) != "sports"]
    share = len(nonsports) / len(closed) if closed else None
    profit = sum(c["pnl"] for c in closed if c["pnl"] > 0)
    biggest = max((c["pnl"] for c in closed), default=0)
    big_share = biggest / profit if profit > 0 else 0
    return {"d7": d7, "d30": d30, "all": all_, "nonsports": share, "big_share": big_share}


def weekly(engine):
    now = time.time()
    rows = []
    traders = [(t["name"], t["wallet"], "core") for t in C.CORE]
    for name in C.WATCH_ONLY_NAMES:
        w = engine.s["watch_wallets"].get(name) or api.find_profile(name)
        if w:
            engine.s["watch_wallets"][name] = w
        traders.append((name, w, "watch"))
    for name, w, kind in traders:
        if not w:
            rows.append((name, kind, None))
            continue
        try:
            rows.append((name, kind, _stats(engine.api.closed_positions(w), now)))
        except api.ApiError:
            rows.append((name, kind, None))
    ranked = sorted(rows, key=lambda r: -(r[2]["d30"][0] if r[2] else -1e18))
    out = ["COPYCAT weekly trader review %s (from each wallet's recent closed positions)" % fmt_ct()[:10],
           "name | list | 7d P&L | 30d P&L, win% | all P&L, win% | non-sports | top win share"]
    flags, proposals = [], []
    for name, kind, st in ranked:
        tag = kind + (" (demoted)" if any(d["name"] == name for d in engine.s["demoted"].values()) else "")
        if not st:
            out.append("%s | %s | data unavailable" % (name, tag))
            continue
        wr = lambda x: "-" if x is None else "%.0f%%" % (100 * x)
        out.append("%s | %s | %+.0f | %+.0f, %s | %+.0f, %s | %s | %.0f%%" % (
            name, tag, st["d7"][0], st["d30"][0], wr(st["d30"][1]), st["all"][0], wr(st["all"][1]),
            wr(st["nonsports"]), 100 * st["big_share"]))
        if st["big_share"] > C.BIG_WIN_SHARE:
            flags.append("%s: one win = %.0f%% of profit" % (name, 100 * st["big_share"]))
        good = st["d30"][0] > 0 and (st["d30"][1] or 0) >= 0.55 and (st["nonsports"] or 0) >= 0.8 \
            and st["big_share"] <= C.BIG_WIN_SHARE
        if kind == "watch" and good:
            proposals.append("ADD %s" % name)
        if kind == "core" and (st["d30"][0] < 0 or (st["d30"][1] is not None and st["d30"][1] < 0.5)):
            proposals.append("DROP %s" % name)
    if flags:
        out.append("One-big-win flags: " + "; ".join(flags))
    out.append("Proposed: " + (", ".join(proposals) if proposals else "no changes") +
               ". Nothing changes until you approve.")
    return "\n".join(out)


def final(engine):
    s = engine.s
    trades = []
    if os.path.exists(engine.trades_csv):
        with open(engine.trades_csv) as f:
            trades = list(csv.DictReader(f))
    closes = [t for t in trades if t["action"] in ("CLOSE", "SETTLE")]
    wins = sum(1 for t in closes if float(t["result"] or 0) > 0)
    by_trader = Counter()
    for t in closes:
        by_trader[t["trader"]] += float(t["result"] or 0)
    eq = engine.equity()
    days = (time.time() - s["started_at"]) / 86400
    lines = ["COPYCAT %d-day PAPER results" % round(days),
             "Equity %.2f from %.2f (%+.2f, %+.1f%%). Realized %+.2f, unrealized %+.2f." % (
                 eq, s["bankroll_start"], eq - s["bankroll_start"], 100 * (eq / s["bankroll_start"] - 1),
                 s["realized_pnl"], engine.unrealized()),
             "Entries %d, closed %d, win rate %s, open now %d." % (
                 sum(1 for t in trades if t["action"] == "OPEN"), len(closes),
                 "%.0f%%" % (100 * wins / len(closes)) if closes else "-", len(s["positions"])),
             "Peak equity %.2f, max drawdown limit %s." % (s["peak_equity"], "HIT" if s["dd_paused"] else "not hit"),
             "By trader: " + (", ".join("%s %+.2f" % kv for kv in by_trader.most_common()) or "no closed trades"),
             "Demoted: " + (", ".join("%s (%s)" % (d["name"], d["reason"]) for d in s["demoted"].values()) or "none"),
             "Still PAPER. Live trading needs you to say \"go live\", and live order code is not built yet."]
    return "\n".join(lines)
