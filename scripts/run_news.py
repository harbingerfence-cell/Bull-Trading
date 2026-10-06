"""News-event backtest (frozen rules) with a matched random-date control.
Usage: PYTHONPATH=. python scripts/run_news.py DAILY_DIR NEWS_DIR [SYMS]
Event -> next tradable open (pre-market article: that day's open; in-session/after: next day's open).
Trade the category's direction; stop 2 ATR(14), target 4 ATR, hold <= 10 trading days.
Dev 2021-2022, test >= 2023. Gate: beats control in BOTH eras and pooled z >= 2.8 (12 categories)."""
import bisect, glob, json, os, random, sys
from datetime import datetime, time, timezone
from statistics import mean, pstdev
from bull_brain.bars import load_csv
from bull_brain.indicators import atr
from bull_brain.news import CATEGORIES, classify
from bull_brain.orb import ET
from bull_brain.patterns import Series, simulate_trade

SPLIT = datetime(2023, 1, 1, tzinfo=timezone.utc)
DAILY, NEWS = sys.argv[1], sys.argv[2]
syms = sys.argv[3].split(",") if len(sys.argv) > 3 else [os.path.basename(f)[:-6] for f in sorted(glob.glob(f"{NEWS}/*.jsonl")) if not os.path.basename(f).startswith("_")]
rng = random.Random(5)
STOP_ATR, TGT_ATR, HOLD = 2.0, 4.0, 10
res = {c: {"dev": [], "test": []} for c in CATEGORIES}
diffs = {c: {"dev": [], "test": []} for c in CATEGORIES}
for sym in syms:
    path = f"{DAILY}/{sym}.csv"
    if not os.path.exists(path):
        continue
    bars = load_csv(path); s = Series(bars); n = len(bars)
    A = atr(bars, 14); dates = [b.ts.date() for b in bars]
    def sim(t, side, cat):
        a = A[t]
        if a is None or t < 20 or t + 2 >= n: return None
        o = bars[t + 1].open
        stop, tgt = (o - STOP_ATR * a, o + TGT_ATR * a) if side == "LONG" else (o + STOP_ATR * a, o - TGT_ATR * a)
        return simulate_trade(sym, s, t, cat, side, stop, tgt, max_hold=HOLD)
    era_idx = {False: [i for i in range(21, n - 3) if bars[i].ts < SPLIT], True: [i for i in range(21, n - 3) if bars[i].ts >= SPLIT]}
    busy = {}
    for line in open(f"{NEWS}/{sym}.jsonl"):
        a = json.loads(line)
        c = classify(a["h"], a["sy"])
        if not c or a["sy"] != [sym]: continue
        cat, side = c
        T = datetime.fromisoformat(a["t"].replace("Z", "+00:00")).astimezone(ET)
        pos = bisect.bisect_left(dates, T.date())
        t = pos if (pos < n and dates[pos] == T.date() and T.time() >= time(9, 30)) else pos - 1
        if t < 20 or t <= busy.get((sym, cat), -1): continue
        tr = sim(t, side, cat)
        if tr is None: continue
        busy[(sym, cat)] = t + tr.bars_held
        is_test = bars[t].ts >= SPLIT
        if bars[t].ts < datetime(2021, 1, 1, tzinfo=timezone.utc): continue
        rs = []
        for _ in range(36):
            x = sim(rng.choice(era_idx[is_test]), side, "ctl")
            if x: rs.append(x.r)
            if len(rs) >= 12: break
        k = "test" if is_test else "dev"
        res[cat][k].append(tr.r)
        if rs: diffs[cat][k].append(tr.r - mean(rs))

def se(x): return pstdev(x) / len(x) ** .5 if len(x) > 1 else float("inf")
print(f"symbols: {len(syms)}  stop {STOP_ATR} ATR / target {TGT_ATR} ATR / hold<={HOLD}d; dev 2021-22, test>=2023\n")
print(f"{'category':15s} dir   {'DEV n/avgR/vs-ctl':28s} {'TEST n/avgR/vs-ctl':28s} pooled-z gate")
passed = []
from bull_brain.news import RULES
dirs = {c: d for c, d, _ in RULES}
for c in CATEGORIES:
    def cell(k):
        r, d = res[c][k], diffs[c][k]
        return (f"{len(r):4d} {mean(r):+.2f} {mean(d):+.2f}±{se(d):.2f}" if r and d else "n=0").ljust(28)
    allp = diffs[c]["dev"] + diffs[c]["test"]
    z = mean(allp) / se(allp) if len(allp) > 2 else float("nan")
    ok = bool(diffs[c]["dev"] and diffs[c]["test"] and mean(diffs[c]["dev"]) > 0 and mean(diffs[c]["test"]) > 0 and z >= 2.8)
    if ok: passed.append(c)
    print(f"{c:15s} {dirs[c]:5s} {cell('dev')} {cell('test')} {z:+6.2f}   {'PASS' if ok else '-'}")
print("\nPASSED:", passed or "none")
