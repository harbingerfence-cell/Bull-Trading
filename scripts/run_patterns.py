"""Frozen-rule chart/candlestick pattern backtest with a matched random-date control.
Usage: PYTHONPATH=. python scripts/run_patterns.py DAILY_DIR
Dev era <= 2022-12-31, held-out test era >= 2023-01-01. No parameters are tuned.
Pre-registered gate: PASS only if the pattern beats the control in BOTH eras AND the pooled z >= 2.5
(26 patterns are tested, so some will look good by chance; z>=2.5 + both-eras is the guard)."""
import glob, os, random, sys
from datetime import datetime, timezone
from statistics import mean, pstdev
from bull_brain.bars import load_csv
from bull_brain.patterns import DETECTORS, HOLD_DAYS, Series, backtest_pattern, simulate_trade

SPLIT = datetime(2023, 1, 1, tzinfo=timezone.utc)
rng = random.Random(7)
data = {os.path.basename(f)[:-4]: load_csv(f) for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.csv")))}
series = {s: Series(b) for s, b in data.items()}
ERA = {}


def era_idx(sym, is_test):
    k = (sym, is_test)
    if k not in ERA:
        ERA[k] = [i for i, b in enumerate(series[sym].b) if i >= 140 and i < len(series[sym].b) - 3 and (b.ts >= SPLIT) == is_test]
    return ERA[k]


def control_r(sym, tr, hold, draws=12):
    s = series[sym]
    t0 = tr.signal_index
    ref = s.b[t0].close
    sp, tp = abs(ref - tr.stop) / ref, abs(tr.target - ref) / ref
    era = era_idx(sym, s.b[t0].ts >= SPLIT)
    rs = []
    for _ in range(draws * 3):
        i = rng.choice(era)
        c = s.b[i].close
        stop, tgt = (c * (1 - sp), c * (1 + tp)) if tr.side == "LONG" else (c * (1 + sp), c * (1 - tp))
        x = simulate_trade(sym, s, i, "ctl", tr.side, stop, tgt, max_hold=hold)
        if x is not None:
            rs.append(x.r)
        if len(rs) >= draws:
            break
    return mean(rs) if rs else None


def se(xs):
    return pstdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else float("inf")


print(f"{len(data)} symbols, {sum(len(b) for b in data.values())} daily bars; dev<=2022, test>=2023\n")
print(f"{'pattern':26s} side  {'DEV n/avgR/vs-ctl':32s} {'TEST n/avgR/vs-ctl':32s} pooled-z  gate")
rows = []
for name in DETECTORS:
    hold = HOLD_DAYS.get(name, 30)
    trs = [t for sym, bars in data.items() for t in (setattr_t for setattr_t in backtest_pattern(sym, bars, name))]
    # attach symbol (backtest_pattern stores it in .symbol)
    parts = {}
    for label, is_test in (("dev", False), ("test", True)):
        T = [t for t in trs if (t.signal_date >= SPLIT) == is_test]
        d = []
        for t in T:
            c = control_r(t.symbol, t, hold)
            if c is not None:
                d.append(t.r - c)
        parts[label] = (T, d)
    (Td, dd), (Tt, dt) = parts["dev"], parts["test"]
    side = trs[0].side if trs else "-"
    def cell(T, d):
        if not T: return f"{'n=0':32s}"
        m = mean(d) if d else float("nan")
        return f"{len(T):4d} {mean(t.r for t in T):+.2f} {m:+.2f}±{se(d):.2f}".ljust(32)
    allд = dd + dt
    z = mean(allд) / se(allд) if len(allд) > 2 and se(allд) not in (0, float("inf")) else float("nan")
    ok = bool(dd and dt and mean(dd) > 0 and mean(dt) > 0 and z >= 2.5)
    rows.append((name, side, z, ok))
    print(f"{name:26s} {side:5s} {cell(Td, dd)} {cell(Tt, dt)} {z:+6.2f}   {'PASS' if ok else '-'}")
print("\nPASSED:", [r[0] for r in rows if r[3]] or "none")
