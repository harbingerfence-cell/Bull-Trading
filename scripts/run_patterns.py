"""Frozen-rule chart-pattern backtest with a matched random-date control.
Usage: PYTHONPATH=. python scripts/run_patterns.py DAILY_DIR
Dev era <= 2022-12-31, held-out test era >= 2023-01-01. No parameters are tuned."""
import glob, os, random, sys
from datetime import datetime, timezone
from statistics import mean, pstdev
from bull_brain.bars import load_csv
from bull_brain.patterns import DETECTORS, Series, backtest_pattern, simulate_trade

SPLIT = datetime(2023, 1, 1, tzinfo=timezone.utc)
rng = random.Random(7)
data = {os.path.basename(f)[:-4]: load_csv(f) for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.csv")))}
series = {s: Series(b) for s, b in data.items()}


def control_r(sym, tr, draws=30):
    """Mean R of random-date trades with the same side / stop % / target % as this trade."""
    s = series[sym]
    n = len(s.b)
    t0 = tr.signal_index
    ref = s.b[t0].close
    sp, tp = abs(ref - tr.stop) / ref, abs(tr.target - ref) / ref
    era = [i for i in range(140, n - 3) if (s.b[i].ts >= SPLIT) == (s.b[t0].ts >= SPLIT)]
    rs = []
    for _ in range(draws * 3):
        i = rng.choice(era)
        c = s.b[i].close
        stop, tgt = (c * (1 - sp), c * (1 + tp)) if tr.side == "LONG" else (c * (1 + sp), c * (1 - tp))
        x = simulate_trade(sym, s, i, "ctl", tr.side, stop, tgt)
        if x is not None:
            rs.append(x.r)
        if len(rs) >= draws:
            break
    return mean(rs) if rs else None


def fmt(xs):
    if not xs: return "n=  0"
    se = pstdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else 0
    return f"n={len(xs):4d} avgR={mean(xs):+.3f}±{se:.3f} win={sum(x>0 for x in xs)/len(xs):.2f}"


print(f"{len(data)} symbols, {sum(len(b) for b in data.values())} daily bars; dev<=2022, test>=2023\n")
allres = {}
for name in DETECTORS:
    trades = []
    for sym, bars in data.items():
        for tr in backtest_pattern(sym, bars, name):
            tr.sym = sym
            trades.append(tr)
    allres[name] = trades
    dev = [t for t in trades if t.signal_date < SPLIT]
    test = [t for t in trades if t.signal_date >= SPLIT]
    side = trades[0].side if trades else "-"
    line = f"{name:24s} {side:5s} DEV {fmt([t.r for t in dev])} | TEST {fmt([t.r for t in test])}"
    diffs = []
    for t in test:
        c = control_r(t.sym, t)
        if c is not None:
            diffs.append(t.r - c)
    if diffs:
        se = pstdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else 0
        line += f" | TEST vs random-date control: {mean(diffs):+.3f}±{se:.3f}R"
    print(line)
