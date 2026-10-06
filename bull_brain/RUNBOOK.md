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

## Swing scanner (3 long chart patterns, paper)
Frozen rules from `scripts/run_patterns.py`; SPY>200d regime filter tested once and rejected.
- After the close (~16:20 ET): `python -m bull_brain.swing scan` -> `swing_queue.json` + journaled notes.
- Next morning (~09:31 ET): `python -m bull_brain.swing open` -> quote check, risk sizing, gate, GTC bracket order (max 3 new/day, 8 open).
- Daily: `python -m bull_brain.swing manage` (30-trading-day time stop). Weekly: `python -m bull_brain.swing score`.
- State lives in `swing_journal.jsonl` / `swing_queue.json` (local files; use a persistent volume if run on a host).

### Event-day signal (added to the swing scanner, long and short)
Frozen rule from `scripts/run_events.py`: volume >= 2.5x prior-20d mean and |close-to-close| >= 4%; trade WITH the move,
stop at the event bar's opposite extreme, target 2R, 20-trading-day time stop. Evidence is weak (shorts beat the random-date
control in dev and test but each period is within noise); it runs on paper as a forward test only.

## Research status (2026-10-06)
Every signal tested so far failed a held-out + matched-random-control gate; the daily swing schedule is DISABLED.
- Opening-range breakout/fade, gaps, crypto session breakouts: negative or noise (1-min IEX bars, Apr-Oct 2026).
- 32 chart/candlestick patterns on 47 large caps AND on 291 random liquid mid/small caps (2016-2026): none passed
  (both-eras beat-control and pooled z >= 2.5). Large-cap "winners" (bull flag, cup & handle) vanished on the wide set.
- Event-day (rvol >= 2.5 & |move| >= 4%): large-cap short looked promising, did not replicate on 291 mid/small caps.
- News headline categories (upgrade/downgrade/PT/EPS/guidance/buyback/offering), 20 large caps 2021-26: none passed.
Caveats: free IEX minute data; survivorship (symbols active today); daily-horizon only for news; no options/short-borrow costs modelled.
Reproduce: scripts/run_patterns.py, run_events.py, run_news.py, run_gap.py, run_variants.py.
