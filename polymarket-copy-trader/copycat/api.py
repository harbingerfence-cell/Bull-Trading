"""Thin read-only clients for Polymarket public APIs. No keys, no order placement."""
import json
import time
import urllib.parse
import urllib.request

DATA = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = "copycat-paper/1.0"


class ApiError(Exception):
    pass


def _get(url, params=None, retries=3, timeout=20):
    if params:
        url = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # network, timeout, bad JSON
            last = e
        time.sleep(1.5 * (i + 1))
    raise ApiError("%s -> %s" % (url, last))


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "results", "trades", "positions"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def g(d, *keys, default=None):
    """Read the first present key; the v2 API is snake_case, v1 camelCase."""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _first_working(paths, params):
    err = None
    for p in paths:
        try:
            return _rows(_get(DATA + p, params))
        except ApiError as e:
            err = e
    raise err


def normalize_trade(r):
    return {
        "wallet": (g(r, "proxy_wallet", "proxyWallet") or "").lower(),
        "ts": int(float(g(r, "timestamp", default=0))),
        "condition_id": g(r, "condition_id", "conditionId", default=""),
        "token_id": str(g(r, "token_id", "asset", default="")),
        "side": (g(r, "side", default="") or "").upper(),
        "size": float(g(r, "size", default=0)),
        "price": float(g(r, "price", default=0)),
        "tx": g(r, "transaction_hash", "transactionHash", default=""),
        "title": g(r, "title", default=""),
        "slug": g(r, "slug", default=""),
        "event_slug": g(r, "event_slug", "eventSlug", default=""),
        "outcome": g(r, "outcome", default=""),
        "type": (g(r, "type", default="TRADE") or "TRADE").upper(),
    }


def recent_trades(wallet, limit=100):
    rows = _first_working(["/v2/trades", "/trades"], {"user": wallet, "limit": limit})
    out = [normalize_trade(r) for r in rows]
    if not out:  # fall back to activity feed, filtered to trades
        rows = _first_working(["/v2/activity", "/activity"], {"user": wallet, "limit": limit})
        out = [normalize_trade(r) for r in rows]
    return [t for t in out if t["type"] == "TRADE" and t["side"] in ("BUY", "SELL") and t["token_id"]]


def positions(wallet, limit=500):
    rows = _first_working(["/v2/positions", "/positions"], {"user": wallet, "limit": limit, "sizeThreshold": 0})
    if len(rows) >= limit:
        return None  # truncated: cannot infer that a missing token was closed
    out = {}
    for r in rows:
        tok = str(g(r, "token_id", "asset", default=""))
        if tok:
            out[tok] = float(g(r, "size", default=0))
    return out


def closed_positions(wallet, limit=500):
    """Resolved/closed positions with realized PnL, used for trader stats."""
    rows = _first_working(["/v2/closed-positions", "/closed-positions"], {"user": wallet, "limit": limit})
    out = []
    for r in rows:
        out.append({
            "condition_id": g(r, "condition_id", "conditionId", default=""),
            "title": g(r, "title", default=""),
            "event_slug": g(r, "event_slug", "eventSlug", default=""),
            "pnl": float(g(r, "realized_pnl", "realizedPnl", default=0)),
            "ts": int(float(g(r, "timestamp", "end_date_ts", default=0) or 0)),
            "end_date": g(r, "end_date", "endDate", default=""),
        })
    return out


_market_cache = {}
_event_cache = {}


def market(condition_id, max_age=600):
    hit = _market_cache.get(condition_id)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    rows = _rows(_get(GAMMA + "/markets", {"condition_ids": condition_id}))
    m = rows[0] if rows else None
    _market_cache[condition_id] = (time.time(), m)
    return m


def event_tags(event_slug, max_age=3600):
    if not event_slug:
        return []
    hit = _event_cache.get(event_slug)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    rows = _rows(_get(GAMMA + "/events", {"slug": event_slug}))
    tags = []
    if rows:
        for t in rows[0].get("tags") or []:
            tags.append((t.get("slug") or t.get("label") or "").lower())
    _event_cache[event_slug] = (time.time(), tags)
    return tags


def book(token_id):
    b = _get(CLOB + "/book", {"token_id": token_id})
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return bids, asks


def find_profile(name):
    """Resolve a username to a proxy wallet via gamma public search. Best effort."""
    try:
        res = _get(GAMMA + "/public-search", {"q": name, "search_profiles": "true", "limit_per_type": 5})
    except ApiError:
        return None
    for p in (res.get("profiles") or []) if isinstance(res, dict) else []:
        if (p.get("name") or "").lower() == name.lower() or (p.get("pseudonym") or "").lower() == name.lower():
            return (p.get("proxyWallet") or p.get("proxy_wallet") or "").lower() or None
    return None
