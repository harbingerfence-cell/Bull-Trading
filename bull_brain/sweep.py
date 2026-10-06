"""Parameter sweep with walk-forward validation.

Rank configs on the TRAIN days only, then report the same configs on the
held-out TEST days. Pick by test results, not train results.
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date
from typing import Any

from .orb import Bar, BacktestResult, ORBConfig, backtest, ET

# Aggressive preset: bigger risk per trade, 4x intraday buying power, 3 trades.
AGGRESSIVE = ORBConfig(or_minutes=5, target_r=2.0, risk_fraction=0.01, max_notional_fraction=4.0,
                       max_trades_per_day=3, slippage_bps=1.0, cost_per_share=0.0)


def split_days(bars: list[Bar], train_fraction: float = 0.6) -> tuple[list[Bar], list[Bar]]:
    days = sorted({b.ts.astimezone(ET).date() for b in bars})
    cut = days[int(len(days) * train_fraction)] if days else None
    return ([b for b in bars if b.ts.astimezone(ET).date() < cut],
            [b for b in bars if b.ts.astimezone(ET).date() >= cut])


def sweep(bars: list[Bar], equity: float, base: ORBConfig = AGGRESSIVE,
          grid: dict[str, list] | None = None, train_fraction: float = 0.6,
          goal: float = 100.0) -> list[dict[str, Any]]:
    grid = grid or {"or_minutes": [5, 15, 30], "target_r": [1.0, 1.5, 2.0, 3.0],
                    "risk_fraction": [0.005, 0.01, 0.02]}
    train, test = split_days(bars, train_fraction)
    keys = list(grid)
    rows = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(keys, combo))
        cfg = replace(base, **params)
        tr = backtest(train, cfg, equity).summary(goal)
        te = backtest(test, cfg, equity).summary(goal)
        rows.append({"params": params, "train": tr, "test": te})
    rows.sort(key=lambda r: (r["train"]["avg_daily_pnl"] or float("-inf")), reverse=True)
    return rows
