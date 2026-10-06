# Paper run runbook

1. Environment: allow `data.alpaca.markets` and `paper-api.alpaca.markets`; set
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` (paper keys).
2. Copy `limits.example.json` to `limits.json` and edit. Every field must be set or the gate blocks execution.
3. Backtest + sweep (needs bars: `bars.fetch_alpaca_bars` or `bars.load_csv`):
   `sweep.sweep(bars, equity)` ranks on train days and reports held-out test days; choose by test.
4. Run: `python -m bull_brain.run_paper --symbol SPY --limits limits.json`
   Start before 09:30 ET; it flattens at the config's `flat_time` and journals to `paper_journal.jsonl`.
5. Kill switch: `AlpacaPaperBroker(...).cancel_all()`.
