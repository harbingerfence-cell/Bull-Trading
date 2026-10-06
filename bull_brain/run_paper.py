"""CLI: python -m bull_brain.run_paper --symbol SPY --limits limits.json [--preset aggressive]

Runs the ORB strategy against Alpaca PAPER. Owner limits are read from a JSON
file (RiskLimits fields); unset limits block execution by design. Keys come
from ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY.
"""
from __future__ import annotations

import argparse
import json
import logging

from .alpaca_broker import AlpacaPaperBroker
from .alpaca_data import AlpacaQuoteProvider
from .bars import fetch_alpaca_bars
from .journal import Journal
from .market_data import MarketDataAdapter
from .models import RiskLimits
from .orb import ORBConfig
from .runner import ORBRunner, run_loop
from .sweep import AGGRESSIVE


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--limits", required=True)
    ap.add_argument("--journal", default="paper_journal.jsonl")
    ap.add_argument("--preset", choices=["aggressive", "default"], default="aggressive")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    limits = RiskLimits(**json.load(open(a.limits)))
    cfg = AGGRESSIVE if a.preset == "aggressive" else ORBConfig()
    provider = AlpacaQuoteProvider()
    broker = AlpacaPaperBroker(limits, Journal(a.journal))
    runner = ORBRunner(a.symbol, cfg, lambda s, st, en: fetch_alpaca_bars(provider, s, st, en),
                       MarketDataAdapter(provider, limits), broker)
    run_loop(runner)


if __name__ == "__main__":
    main()
