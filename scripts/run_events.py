"""Event-day continuation test (frozen rules). Usage: PYTHONPATH=. python scripts/run_events.py DAILY_DIR
Event day: volume >= 2.5x its prior-20-day mean AND |close/prev_close - 1| >= 4%.
Trade WITH the move: enter next open; stop = event bar's opposite extreme; target 2R; max hold 20 days."""
import glob, os, random, sys
from datetime import datetime, timezone
from statistics import mean, pstdev
from bull_brain.bars import load_csv
from bull_brain.patterns import Series, simulate_trade

SPLIT = datetime(2023, 1, 1, tzinfo=timezone.utc)
rng = random.Random(11)
data = {os.path.basename(f)[:-4]: load_csv(f) for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.csv")))}
RVOL, MOVE, HOLD, RR = 2.5, 0.04, 20, 2.0


def events(bars):
    out, t = [], 25
    while t < len(bars) - 2:
        b, p = bars[t], bars[t - 1]
        vavg = sum(x.volume for x in bars[t - 20:t]) / 20
        ret = b.close / p.close - 1
        if vavg > 0 and b.volume >= RVOL * vavg and abs(ret) >= MOVE:
            yield t, ("LONG" if ret > 0 else "SHORT")
        t += 1


def fmt(xs):
    if not xs: return "n=0"
    se = pstdev(xs) / len(xs) ** .5 if len(xs) > 1 else 0
    return f"n={len(xs):4d} avgR={mean(xs):+.3f}±{se:.3f} win={sum(x>0 for x in xs)/len(xs):.2f}"


def control(s, tr, t0, draws=30):
    ref = s.b[t0].close
    sp, tp = abs(ref - tr.stop) / ref, abs(tr.target - ref) / ref
    era = [i for i in range(25, len(s.b) - 3) if (s.b[i].ts >= SPLIT) == (s.b[t0].ts >= SPLIT)]
    rs = []
    for _ in range(draws * 3):
        i = rng.choice(era); c = s.b[i].close
        stop, tgt = (c * (1 - sp), c * (1 + tp)) if tr.side == "LONG" else (c * (1 + sp), c * (1 - tp))
        x = simulate_trade("c", s, i, "ctl", tr.side, stop, tgt, max_hold=HOLD)
        if x: rs.append(x.r)
        if len(rs) >= draws: break
    return mean(rs) if rs else None


res = {"LONG": [], "SHORT": []}
for sym, bars in data.items():
    s = Series(bars)
    busy_until = -1
    for t, side in events(bars):
        if t <= busy_until: continue
        b = bars[t]
        stop = b.low if side == "LONG" else b.high
        risk0 = abs(b.close - stop)
        target = b.close + RR * risk0 if side == "LONG" else b.close - RR * risk0
        tr = simulate_trade(sym, s, t, "event", side, stop, target, max_hold=HOLD)
        if tr is None: continue
        busy_until = t + tr.bars_held
        tr.c = control(s, tr, t)
        tr.sym = sym
        res[side].append(tr)

print(f"{len(data)} symbols; event = rvol>={RVOL} & |move|>={MOVE:.0%}; hold<={HOLD}d; target {RR}R; dev<=2022 test>=2023\n")
for side, trs in res.items():
    for era, sel in (("DEV ", lambda t: t.signal_date < SPLIT), ("TEST", lambda t: t.signal_date >= SPLIT)):
        T = [t for t in trs if sel(t)]
        d = [t.r - t.c for t in T if t.c is not None]
        extra = f" | vs random-date control {mean(d):+.3f}±{pstdev(d)/len(d)**.5:.3f}R" if len(d) > 1 else ""
        print(f"{side:5s} {era} {fmt([t.r for t in T])}{extra}")
