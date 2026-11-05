# COPYCAT (paper mode only)

Mirrors BUY trades from a small Polymarket watchlist into a simulated $1,000 bankroll with hard risk limits.
No keys, no wallet, no order placement exist in this code. Live trading is not implemented.

## Run
    python -m copycat once     # one cycle
    python -m copycat run      # every 5 min; daily report 8 PM CT; Sunday trader review; 14-day results
    python -m unittest discover tests

## Data
- Trades: data-api.polymarket.com `/v2/trades` (falls back to `/trades`, then `/v2/activity`)
- Positions / closed positions: data-api `/v2/positions`, `/v2/closed-positions` (fall back to v1 paths)
- Market metadata, tags, end date, liquidity: gamma-api.polymarket.com
- Fill simulation: clob.polymarket.com `/book` (walks the book for a realistic paper fill)
- polymarketanalytics.com renders its stats with JavaScript, so win rate / category stats are computed
  from Polymarket's own closed-positions data instead.

## Files (in COPYCAT_DATA_DIR, `/data` on Railway volume)
- `trades.csv`: time (CT), action, trader, market, side, our price, their price, size, shares, result, running P&L
- `skips.csv`: every skipped fresh BUY with the reason
- `state.json`, `outbox.log` (every report and alert)

## Alerts / reports
Set one of: `NTFY_TOPIC`, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`.
Bankroll: `COPYCAT_BANKROLL` (default 1000).

## Rule interpretations
- "Watchlist traders" for the 1.5x confirmation = the six core traders.
- Per-trade cap for the 1.5x boost is the $50 max (so $20 becomes $30 on a $1,000 bankroll).
- Sizing and limits use realized bankroll (start + realized P&L). Drawdown uses marked equity vs peak.
- Drawdown pause stops new entries; exits that mirror a trader's sell and resolutions still process,
  since holding a position the trader has dumped adds risk. Clear with `python -m copycat resume`.
- Categories outside politics/elections, economics/Fed, crypto, geopolitics, culture are skipped.
- Win-rate demotion needs at least 10 closed positions in 30 days.
