"""Experiments: breakout vs fade, with filters, on stocks and crypto.
Usage: python scripts/run_variants.py DATA_DIR   (CSV per symbol: SPY QQQ BTC ETH)
Picks nothing automatically: prints train-ranked configs with held-out test stats."""
import itertools, sys
from datetime import time
from bull_brain.bars import load_csv
from bull_brain.variants import VConfig, backtest_v, split_by_date

EQ = 100_000
DATA = sys.argv[1]


def grid(markets):
    for mode, filters, ors, third in markets:
        for f, o, x in itertools.product(filters, ors, third):
            yield mode, f, o, x


def configs(base: dict):
    for f, o, x in itertools.product([(), ("vwap",), ("gap",), ("vwap", "gap")], [5, 15, 30], [1.0, 1.5, 2.0]):
        if "gap" in f and not base.get("gap_ok", True): continue
        if "vwap" in f and False: continue
        yield VConfig(mode="breakout", filters=f, or_minutes=o, target_r=x, **{k: v for k, v in base.items() if k != "gap_ok"})
    for f, o, x in itertools.product([(), ("gap",)], [5, 15, 30], [0.5, 1.0, 2.0]):
        if "gap" in f and not base.get("gap_ok", True): continue
        yield VConfig(mode="fade", filters=f, or_minutes=o, stop_mult=x, **{k: v for k, v in base.items() if k != "gap_ok"})


def label(c):
    return f"{c.mode:8s} f={'+'.join(c.filters) or '-':9s} or={c.or_minutes:2d} {'tgt' if c.mode=='breakout' else 'stopx'}={c.target_r if c.mode=='breakout' else c.stop_mult}"


def run(name, symbols, base):
    for sym in symbols:
        bars = load_csv(f"{DATA}/{sym}.csv")
        tz = base.get("tz", "America/New_York")
        tr_b, te_b = split_by_date(bars, tz)
        rows = []
        for c in configs(base):
            tr, te = backtest_v(tr_b, c, EQ).summary(100), backtest_v(te_b, c, EQ).summary(100)
            rows.append((c, tr, te))
        both = [r for r in rows if (r[1]["total_pnl"] or 0) > 0 and (r[2]["total_pnl"] or 0) > 0]
        print(f"\n=== {name} {sym}: {len(rows)} configs, positive in BOTH train & test: {len(both)} ===")
        by_mode = {m: sum(1 for r in both if r[0].mode == m) for m in ("breakout", "fade")}
        print("   by mode:", by_mode)
        for c, tr, te in sorted(rows, key=lambda r: -(r[1]["avg_daily_pnl"] or -1e9))[:4]:
            print(f"  TRAIN-best {label(c)} | train/day {tr['avg_daily_pnl']:8.1f} ({tr['trades']:3d}t) | TEST/day {te['avg_daily_pnl']:8.1f} ({te['trades']:3d}t) win {te['win_rate'] or 0:.2f} avgR {te['avg_r'] or 0:+.2f}")
        for c, tr, te in sorted(both, key=lambda r: -(r[2]["avg_daily_pnl"] or -1e9))[:3]:
            print(f"  BOTH-positive {label(c)} | train/day {tr['avg_daily_pnl']:8.1f} ({tr['trades']:3d}t) | TEST/day {te['avg_daily_pnl']:8.1f} ({te['trades']:3d}t)")


stock = dict(slippage_bps=1.0, fee_bps=0.0, max_notional_fraction=4.0, risk_fraction=0.005)
run("STOCK", ["SPY", "QQQ"], stock)

for fee in (0.0, 25.0):
    for tzname, ot, lab in (("America/New_York", time(9, 30), "ET-open"), ("UTC", time(0, 0), "UTC-midnight")):
        crypto = dict(tz=tzname, open_time=ot, hold_minutes=180, entry_window_minutes=120, slippage_bps=5.0, fee_bps=fee,
                      fractional=True, max_notional_fraction=1.0, max_stop_pct=0.05, min_stop_pct=0.001,
                      risk_fraction=0.005, gap_ok=False)
        run(f"CRYPTO {lab} fee={fee:g}bps", ["BTC", "ETH"], crypto)
