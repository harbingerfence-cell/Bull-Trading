# Paper run runbook

1. Environment: allow `data.alpaca.markets` and `paper-api.alpaca.markets`; set
   `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` (paper keys).
2. Copy `limits.example.json` to `limits.json` and edit. Every field must be set or the gate blocks execution.
3. Backtest + sweep (needs bars: `bars.fetch_alpaca_bars` or `bars.load_csv`):
   `sweep.sweep(bars, equity)` ranks on train days and reports held-out test days; choose by test.
4. Run: `python -m bull_brain.run_paper --symbol SPY --limits limits.json`
   Start before 09:30 ET; it flattens at the config's `flat_time` and journals to `paper_journal.jsonl`.
5. Kill switch: `AlpacaPaperBroker(...).cancel_all()`.

## TradingView webhook (paper)
1. Env: `TV_WEBHOOK_SECRET` (>=16 chars), `TV_PATH_TOKEN` (random string), Alpaca keys, `PORT`.
2. Run: `python -m bull_brain.webhook --limits limits.json --symbols SPY,QQQ,AAPL`
   (needs a public HTTPS URL, e.g. deploy on Railway; this cloud sandbox is not reachable from TradingView).
3. TradingView alert -> Notifications -> Webhook URL: `https://<host>/tv/<TV_PATH_TOKEN>` (webhooks need a paid TradingView plan).
4. Alert message (JSON): see the template in `bull_brain/webhook.py` docstring. Use either `stop` or `stop_pct`.
   The alert's price is never used: the server fetches a fresh quote, sizes from `risk_per_trade_fraction`, and sends the plan through the risk gate.
5. Safeguards: constant-time secret check, symbol allow-list, alert-age limit, per-day alert cap, duplicate `alert_id` rejection, 8 KB body cap.

## Indicators
`bull_brain/indicators.py`: sma, ema, rsi (Wilder), atr (Wilder), session_vwap, relative_volume, computed from bars.
