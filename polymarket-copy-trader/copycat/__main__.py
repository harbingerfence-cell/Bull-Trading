"""COPYCAT entry point.

  python -m copycat run      # loop forever: cycle every 5 min, daily report 8 PM CT, Sunday review, day-14 report
  python -m copycat once     # one cycle, print errors
  python -m copycat report   # print today's report without sending or resetting
  python -m copycat weekly   # print the trader review
  python -m copycat resume   # clear a drawdown pause (only on Mr. Jackson's instruction)
"""
import sys
import time
import traceback

from . import config as C, notify, reports
from .engine import Engine, now_ct


def scheduled(engine):
    s = engine.s
    t = now_ct()
    today = t.strftime("%Y-%m-%d")
    if t.hour >= C.DAILY_REPORT_HOUR_CT and s["last_daily_report"] != today:
        engine.check_winrates()
        notify.send(reports.daily(engine))
        if t.weekday() == 6 and s["last_weekly_review"] != today:
            notify.send(reports.weekly(engine))
            s["last_weekly_review"] = today
        if not s["final_report_sent"] and time.time() - s["started_at"] >= C.PAPER_RUN_DAYS * 86400:
            notify.send(reports.final(engine))
            s["final_report_sent"] = True
        s["last_daily_report"] = today
        s["period"] = Engine._new_period()
        s["period"]["start_equity"] = engine.equity()
        engine.save()


def selftest(eng):
    """Exercise every read endpoint once against a real recent trade. Read-only, places nothing."""
    from . import api
    from .markets import classify, simulate_buy
    out = []
    try:
        w = C.CORE[0]["wallet"]
        trades = api.recent_trades(w)
        t = next(x for x in trades if x["side"] == "BUY")
        m = api.market(t["condition_id"])
        out.append("market: %s | end %s | liq %s | closed %s" % (
            (m or {}).get("question", "NONE")[:60], (m or {}).get("endDate"), (m or {}).get("liquidityNum"),
            (m or {}).get("closed")))
        tags = api.event_tags(t["event_slug"])
        out.append("tags %s -> %s" % (tags[:6], classify(tags, t["title"])))
        bids, asks = api.book(t["token_id"])
        out.append("book: best bid %s best ask %s; $20 fill %s" % (
            bids[0] if bids else None, asks[0] if asks else None, simulate_buy(asks, 20)[1:] if asks else None))
        pos = api.positions(w)
        out.append("positions: %s, nonzero sizes %s" % ("truncated" if pos is None else len(pos), "-" if pos is None else sum(1 for v in pos.values() if v > 0)))
        cl = api.closed_positions(w)
        out.append("closed positions: %d, sample %s" % (len(cl), cl[0] if cl else None))
    except Exception as e:
        out.append("SELFTEST FAILED: %r" % e)
    for line in out:
        print("  selftest " + line, flush=True)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    eng = Engine(notifier=notify.send)
    last_err_alert = 0
    if cmd == "once":
        errs = eng.run_cycle()
        print("errors:", errs or "none")
        print("fetched:", eng.last_fetch)
        print(reports.daily(eng))
    elif cmd == "selftest":
        selftest(eng)
    elif cmd == "report":
        print(reports.daily(eng))
    elif cmd == "weekly":
        print(reports.weekly(eng))
        eng.save()
    elif cmd == "final":
        print(reports.final(eng))
    elif cmd == "resume":
        eng.s["dd_paused"] = False
        eng.s["peak_equity"] = eng.equity()
        eng.save()
        print("drawdown pause cleared; peak reset to current equity")
    elif cmd == "run":
        print("COPYCAT PAPER mode starting. Bankroll %.2f." % eng.s["bankroll_start"], flush=True)
        selftest(eng)
        while True:
            t0 = time.time()
            try:
                errs = eng.run_cycle()
                scheduled(eng)
                print("%s cycle ok, %d open, equity %.2f, errors %d" % (
                    now_ct().strftime("%H:%M"), len(eng.s["positions"]), eng.equity(), len(errs)), flush=True)
                print("  fetched (trades, newest age min): " + "; ".join(
                    "%s %s/%s" % (k, n, a) for k, (n, a) in eng.last_fetch.items()), flush=True)
                for e in errs[:3]:
                    print("  error: " + e[:300], flush=True)
            except Exception:
                traceback.print_exc()
                if time.time() - last_err_alert > 3600:
                    last_err_alert = time.time()
                    notify.send("COPYCAT: internal error in cycle.\n" + traceback.format_exc()[-800:], urgent=True)
            time.sleep(max(5, C.POLL_SEC - (time.time() - t0)))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
