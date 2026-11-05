"""COPYCAT configuration. Hard risk limits live here and are not changed at runtime."""
import os

MODE = os.environ.get("COPYCAT_MODE", "PAPER").upper()
if MODE != "PAPER":
    # Live trading is deliberately not implemented. Going live requires Mr. Jackson's
    # explicit "go live" plus new code for order signing; this flag alone does nothing.
    raise SystemExit("COPYCAT: only PAPER mode exists. Refusing to start in mode %r." % MODE)

DATA_DIR = os.environ.get("COPYCAT_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
TIMEZONE = "America/Chicago"

# Bankroll is the one value Mr. Jackson may change.
STARTING_BANKROLL = float(os.environ.get("COPYCAT_BANKROLL", "1000"))

# ---- Watchlist ----------------------------------------------------------------
# only_categories: None = all allowed non-sports categories; else a subset.
CORE = [
    {"name": "scottilicious", "wallet": "0x000d257d2dc7616feaef4ae0f14600fdf50a758e", "only_categories": None},
    {"name": "The Spirit of Ukraine>UMA", "wallet": "0x0c0e270cf879583d6a0142fc817e05b768d0434e", "only_categories": None},
    {"name": "bernardbulletin", "wallet": "0x1c144e30f405a25f991cbd8baa15d40599090869", "only_categories": None},
    {"name": "0xc8b9a3", "wallet": "0xc8b9a30184244d427169cf62485dde6041b2b836", "only_categories": None},
    {"name": "SlavaUkraini", "wallet": "0xe899b5ea69afb161da7a35597b6fe70398860899", "only_categories": None},
    {"name": "0x448861", "wallet": "0x448861155279dbf833d041b963e3ac854599e319", "only_categories": {"politics"}},
]
# Watch-only: tracked for the weekly review, never copied. Wallets resolved by name at runtime.
WATCH_ONLY_NAMES = ["elmcap2", "aenews2", "mr.ozi", "Dropper", "morito181"]

# ---- Copy rules ---------------------------------------------------------------
COPY_WINDOW_SEC = 10 * 60          # only copy BUYs placed in the last 10 minutes
MAX_SLIPPAGE = 0.03                # skip if our fill is > 3c worse than theirs
MIN_TIME_TO_RESOLVE_SEC = 24 * 3600
MIN_LIQUIDITY = 50_000
MAX_PRICE = 0.92
MIN_PRICE = 0.05
CONFIRM_WINDOW_SEC = 24 * 3600
CONFIRM_MULT = 1.5
ALLOWED_CATEGORIES = {"politics", "economics", "crypto", "geopolitics", "culture"}

# ---- Hard risk limits (never override) ---------------------------------------
PER_TRADE_PCT = 0.02
PER_TRADE_MAX = 50.0
PER_MARKET_PCT = 0.05
MAX_EXPOSURE_PCT = 0.40
DAILY_LOSS_PCT = 0.05
DRAWDOWN_PCT = 0.15
DEMOTE_LOSS_STREAK = 3
DEMOTE_MIN_WINRATE = 0.50
DEMOTE_WINRATE_DAYS = 30
DEMOTE_MIN_SAMPLE = 10             # need at least this many closed positions in 30d to judge win rate
BIG_WIN_SHARE = 0.25

# ---- Schedule ------------------------------------------------------------------
POLL_SEC = 5 * 60
DAILY_REPORT_HOUR_CT = 20
PAPER_RUN_DAYS = 14
