"""Pooled gap experiment. Usage: PYTHONPATH=. python scripts/run_gap.py DATA_DIR"""
import itertools, sys
from datetime import time
from statistics import mean, pstdev
from bull_brain.bars import load_csv
from bull_brain.gap import GapConfig, gap_backtest

DATA = sys.argv[1]
SYMS = "SPY QQQ IWM AAPL MSFT NVDA TSLA AMD META AMZN GOOGL NFLX AVGO COIN PLTR MU JPM".split()
data = {s: load_csv(f"{DATA}/{s}.csv") for s in SYMS}
days = sorted({t.day for s in SYMS for t in gap_backtest(data[s], GapConfig(min_gap_pct=0))})
cut = days[int(len(days) * 0.6)]


def stats(ts):
    rs = [t.r_multiple for t in ts]
    if not rs: return "n=0"
    se = pstdev(rs) / len(rs) ** 0.5 if len(rs) > 1 else 0
    return f"n={len(rs):4d} avgR={mean(rs):+.3f}±{se:.3f} win={sum(r>0 for r in rs)/len(rs):.2f}"


rows = []
for mode, gmin, tr, hold in itertools.product(["go", "fade"], [0.005, 0.01, 0.02], [1.0, 2.0], [time(11, 0), time(15, 55)]):
    if mode == "fade" and tr != 1.0: continue  # fade target is the prior close
    cfg = GapConfig(mode=mode, min_gap_pct=gmin, target_r=tr, hold_until=hold)
    ts = [t for s in SYMS for t in gap_backtest(data[s], cfg)]
    a, b = [t for t in ts if t.day < cut], [t for t in ts if t.day >= cut]
    rows.append((mode, gmin, tr, hold, a, b))
print(f"split date {cut}; R-multiples net of 2bp slippage per side; ± is std error\n")
for mode, gmin, tr, hold, a, b in rows:
    print(f"{mode:4s} gap>={gmin:.3f} tgt={tr if mode=='go' else 'fill'} hold->{hold:%H:%M} | TRAIN {stats(a)} | TEST {stats(b)}")
