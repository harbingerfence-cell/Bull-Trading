"""Market classification and order-book fill simulation."""
import re

SPORTS_TAGS = {
    "sports", "nba", "nfl", "mlb", "nhl", "wnba", "ncaa", "ncaab", "ncaaf", "cbb", "cfb", "soccer", "football",
    "epl", "premier-league", "champions-league", "la-liga", "serie-a", "bundesliga", "mls", "uefa", "fifa",
    "world-cup", "tennis", "golf", "pga", "ufc", "mma", "boxing", "f1", "formula-1", "nascar", "cricket",
    "ipl", "esports", "olympics", "baseball", "basketball", "hockey", "rugby", "chess", "games", "cs2",
    "dota", "league-of-legends", "valorant", "horse-racing", "cycling", "darts",
}
SPORTS_WORDS = re.compile(
    r"\b(nba|nfl|mlb|nhl|wnba|ufc|premier league|champions league|super bowl|world series|stanley cup|"
    r"grand slam|wimbledon|grand prix|o/u \d|spread:|touchdown|playoffs?|ncaa|fc\b)", re.I)

CATEGORY_TAGS = [  # priority order
    ("politics", {"politics", "elections", "us-politics", "us-election", "us-elections", "global-elections",
                  "world-elections", "trump", "congress", "senate", "house", "primaries", "midterms",
                  "presidential-election", "governor", "mayor", "supreme-court", "scotus", "cabinet"}),
    ("geopolitics", {"geopolitics", "world", "ukraine", "russia", "israel", "gaza", "middle-east", "china",
                     "iran", "taiwan", "foreign-policy", "war", "nato", "venezuela", "ceasefire"}),
    ("economics", {"economy", "economics", "fed", "fed-rates", "fomc", "finance", "inflation", "cpi",
                   "recession", "gdp", "jobs", "interest-rates", "tariffs", "business", "stocks", "ipos"}),
    ("crypto", {"crypto", "bitcoin", "btc", "ethereum", "eth", "solana", "crypto-prices", "xrp", "memecoins",
                "stablecoins", "etf"}),
    ("culture", {"culture", "pop-culture", "entertainment", "movies", "music", "celebrities", "awards",
                 "oscars", "grammys", "tv", "mentions", "tweet-markets", "elon-musk", "youtube", "box-office"}),
]
TITLE_HINTS = [
    ("politics", re.compile(r"\b(election|president|senate|house|governor|mayor|trump|democrat|republican|"
                            r"primary|nominee|vote|parliament|prime minister)\b", re.I)),
    ("geopolitics", re.compile(r"\b(ukraine|russia|putin|zelensky|israel|gaza|iran|china|taiwan|ceasefire|"
                               r"nato|invade|strike)\b", re.I)),
    ("economics", re.compile(r"\b(fed|rate cut|interest rate|inflation|cpi|gdp|recession|unemployment|"
                             r"tariff|fomc)\b", re.I)),
    ("crypto", re.compile(r"\b(bitcoin|btc|ethereum|eth|solana|crypto|xrp|doge)\b", re.I)),
    ("culture", re.compile(r"\b(oscar|grammy|album|movie|box office|tweet|spotify|billboard|celebrity)\b", re.I)),
]


def classify(tags, title):
    """Return 'sports', one of the allowed categories, or 'other'."""
    tags = {t.lower() for t in tags or []}
    if tags & SPORTS_TAGS or SPORTS_WORDS.search(title or ""):
        return "sports"
    for cat, names in CATEGORY_TAGS:
        if tags & names:
            return cat
    for cat, rx in TITLE_HINTS:
        if rx.search(title or ""):
            return cat
    return "other"


def simulate_buy(asks, usd):
    """Walk the ask side. Returns (shares, avg_price, usd_spent)."""
    shares = spent = 0.0
    for price, size in asks:
        if spent >= usd - 1e-9:
            break
        take_usd = min(usd - spent, price * size)
        shares += take_usd / price
        spent += take_usd
    return shares, (spent / shares if shares else 0.0), spent


def simulate_sell(bids, shares):
    """Walk the bid side. Returns (shares_sold, avg_price, usd_received)."""
    sold = got = 0.0
    for price, size in bids:
        if sold >= shares - 1e-9:
            break
        q = min(shares - sold, size)
        sold += q
        got += q * price
    return sold, (got / sold if sold else 0.0), got
