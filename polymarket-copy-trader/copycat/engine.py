"""Paper copy-trading engine: rules, sizing, hard risk limits, state, CSV log."""
import csv
import json
import os
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import api, config as C
from .markets import classify, simulate_buy, simulate_sell

CT = ZoneInfo(C.TIMEZONE)
TRADE_COLS = ["time_ct", "action", "trader", "market", "side", "our_price", "their_price",
              "size_usd", "shares", "result", "running_pnl", "note"]
SKIP_COLS = ["time_ct", "trader", "market", "side", "their_price", "reason"]


def now_ct(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), CT)


def fmt_ct(ts=None):
    return now_ct(ts).strftime("%Y-%m-%d %H:%M:%S CT")


class Engine:
    def __init__(self, data_dir=C.DATA_DIR, client=api, notifier=None):
        self.dir = os.path.abspath(data_dir)
        os.makedirs(self.dir, exist_ok=True)
        self.api = client
        self.notify = notifier or (lambda text, urgent=False: print(("[ALERT] " if urgent else "") + text))
        self.state_path = os.path.join(self.dir, "state.json")
        self.trades_csv = os.path.join(self.dir, "trades.csv")
        self.skips_csv = os.path.join(self.dir, "skips.csv")
        self.s = self._load()

    # ---------------------------------------------------------------- state
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return json.load(f)
        t = time.time()
        return {
            "started_at": t, "bankroll_start": C.STARTING_BANKROLL, "realized_pnl": 0.0,
            "peak_equity": C.STARTING_BANKROLL, "day": now_ct(t).strftime("%Y-%m-%d"),
            "day_start_equity": C.STARTING_BANKROLL, "daily_halt": False, "dd_paused": False,
            "seen_tx": {}, "entries": {}, "signals": {}, "positions": [], "trader": {}, "demoted": {},
            "period": self._new_period(), "api_fail_streak": 0, "last_api_alert": 0,
            "last_weekly_review": "", "final_report_sent": False,
            "watch_wallets": {},
            # don't fire a daily report on the first start if it's already past 8 PM CT
            "last_daily_report": now_ct(t).strftime("%Y-%m-%d") if now_ct(t).hour >= C.DAILY_REPORT_HOUR_CT else "",
        }

    @staticmethod
    def _new_period():
        return {"copied": [], "skipped": [], "closed": [], "limit_hits": [], "notes": [], "start_equity": None}

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=1)
        os.replace(tmp, self.state_path)

    # ---------------------------------------------------------------- money
    def bankroll(self):
        return self.s["bankroll_start"] + self.s["realized_pnl"]

    def open_cost(self, condition_id=None):
        return sum(p["cost"] for p in self.s["positions"] if condition_id in (None, p["condition_id"]))

    def unrealized(self):
        return sum(p["shares"] * p["mark"] - p["cost"] for p in self.s["positions"])

    def equity(self):
        return self.bankroll() + self.unrealized()

    # ---------------------------------------------------------------- logging
    def _csv(self, path, cols, row):
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in cols})

    def _skip(self, trader, t, reason):
        row = {"time_ct": fmt_ct(), "trader": trader, "market": t["title"], "side": t["outcome"],
               "their_price": round(t["price"], 4), "reason": reason}
        self._csv(self.skips_csv, SKIP_COLS, row)
        self.s["period"]["skipped"].append({"trader": trader, "market": t["title"], "reason": reason})

    def _limit_hit(self, what):
        hits = self.s["period"]["limit_hits"]
        if what not in hits:
            hits.append(what)

    # ---------------------------------------------------------------- risk
    def roll_day(self):
        today = now_ct().strftime("%Y-%m-%d")
        if today != self.s["day"]:
            self.s["day"] = today
            self.s["day_start_equity"] = self.equity()
            self.s["daily_halt"] = False

    def check_limits(self):
        eq = self.equity()
        self.s["peak_equity"] = max(self.s["peak_equity"], eq)
        day_pnl = eq - self.s["day_start_equity"]
        if not self.s["daily_halt"] and day_pnl <= -C.DAILY_LOSS_PCT * self.bankroll_for_limits():
            self.s["daily_halt"] = True
            self._limit_hit("daily loss limit")
            self.notify("COPYCAT: daily loss limit hit (%.2f today). No new trades until tomorrow CT." % day_pnl,
                        urgent=True)
        dd = eq - self.s["peak_equity"]
        if not self.s["dd_paused"] and dd <= -C.DRAWDOWN_PCT * self.s["peak_equity"]:
            self.s["dd_paused"] = True
            self._limit_hit("drawdown pause")
            self.notify("COPYCAT: drawdown limit hit. Equity %.2f, peak %.2f (%.1f%%). All new trading paused "
                        "until you reset it." % (eq, self.s["peak_equity"], 100 * dd / self.s["peak_equity"]),
                        urgent=True)

    def bankroll_for_limits(self):
        # Daily-loss and sizing use the realized bankroll so open losses cannot inflate limits.
        return self.bankroll()

    # ---------------------------------------------------------------- marks / settlement
    def mark_positions(self):
        for p in list(self.s["positions"]):
            m = self.api.market(p["condition_id"])
            if m and m.get("closed"):
                prices = _json_list(m.get("outcomePrices"))
                toks = [str(x) for x in _json_list(m.get("clobTokenIds"))]
                if p["token_id"] in toks and prices:
                    px = float(prices[toks.index(p["token_id"])])
                    if px >= 0.99 or px <= 0.01:
                        self._close(p, round(px), "SETTLE", "market resolved")
                        continue
            try:
                bids, asks = self.api.book(p["token_id"])
                if bids:
                    p["mark"] = bids[0][0]  # liquidation value, conservative
            except api.ApiError:
                pass  # keep last mark

    # ---------------------------------------------------------------- trader stats
    def _trader(self, wallet):
        return self.s["trader"].setdefault(wallet, {"streak": 0, "wins": 0, "losses": 0, "pnl": 0.0})

    def _demote(self, wallet, name, reason):
        if wallet in self.s["demoted"]:
            return
        self.s["demoted"][wallet] = {"name": name, "reason": reason, "at": fmt_ct()}
        self.s["period"]["notes"].append("%s moved to watch-only: %s" % (name, reason))

    def check_winrates(self):
        cutoff = time.time() - C.DEMOTE_WINRATE_DAYS * 86400
        for t in C.CORE:
            w = t["wallet"]
            if w in self.s["demoted"]:
                continue
            try:
                closed = [c for c in self.api.closed_positions(w) if c["ts"] >= cutoff]
            except api.ApiError:
                continue
            if len(closed) >= C.DEMOTE_MIN_SAMPLE:
                wr = sum(1 for c in closed if c["pnl"] > 0) / len(closed)
                if wr < C.DEMOTE_MIN_WINRATE:
                    self._demote(w, t["name"], "30-day win rate %.0f%% on %d closed positions" % (100 * wr, len(closed)))

    # ---------------------------------------------------------------- open / close
    def _close(self, p, price, action, note, their_price=""):
        proceeds = p["shares"] * price
        pnl = proceeds - p["cost"]
        self.s["realized_pnl"] += pnl
        self.s["positions"].remove(p)
        self._csv(self.trades_csv, TRADE_COLS, {
            "time_ct": fmt_ct(), "action": action, "trader": p["trader"], "market": p["title"], "side": p["outcome"],
            "our_price": round(price, 4), "their_price": their_price, "size_usd": round(proceeds, 2),
            "shares": round(p["shares"], 4), "result": round(pnl, 2), "running_pnl": round(self.s["realized_pnl"], 2),
            "note": note})
        self.s["period"]["closed"].append({"trader": p["trader"], "market": p["title"], "pnl": round(pnl, 2)})
        st = self._trader(p["wallet"])
        st["pnl"] += pnl
        if pnl > 0:
            st["wins"] += 1
            st["streak"] = 0
        else:
            st["losses"] += 1
            st["streak"] += 1
            if st["streak"] >= C.DEMOTE_LOSS_STREAK:
                self._demote(p["wallet"], p["trader"], "%d copied losses in a row" % st["streak"])

    def _exit(self, p, reason, their_price=""):
        try:
            bids, _ = self.api.book(p["token_id"])
        except api.ApiError:
            return False
        sold, avg, _ = simulate_sell(bids, p["shares"])
        if sold < p["shares"] * 0.999 or avg <= 0:
            # thin book: settle remainder at best bid (or 0 if none) so paper results stay conservative
            avg = ((sold * avg) + (p["shares"] - sold) * (bids[0][0] if bids else 0)) / p["shares"]
        self._close(p, avg, "CLOSE", reason, their_price)
        return True

    def handle_exits(self, wallet, trades, held_positions):
        mine = [p for p in self.s["positions"] if p["wallet"] == wallet]
        if not mine:
            return
        for p in list(mine):
            sell = next((t for t in trades if t["side"] == "SELL" and t["token_id"] == p["token_id"]
                         and t["ts"] >= p["opened_ts"] - 60), None)
            if sell:
                self._exit(p, "trader sold", round(sell["price"], 4))
            elif (held_positions is not None and held_positions.get(p["token_id"], 0) < 0.01
                  and time.time() - p["opened_ts"] > 15 * 60):  # allow for positions-API lag
                self._exit(p, "trader no longer holds position")

    def _record_signal(self, wallet, t):
        key = t["token_id"]
        sig = self.s["signals"].setdefault(key, {})
        if wallet not in sig or t["ts"] < sig[wallet]:
            sig[wallet] = t["ts"]

    def _confirmed(self, wallet, t):
        sig = self.s["signals"].get(t["token_id"], {})
        return any(w != wallet and abs(ts - t["ts"]) <= C.CONFIRM_WINDOW_SEC for w, ts in sig.items())

    def evaluate_buy(self, trader, t):
        wallet, name = trader["wallet"], trader["name"]
        now = time.time()
        if wallet in self.s["demoted"]:
            return self._skip(name, t, "trader is watch-only (demoted)")
        if self.s["dd_paused"]:
            return self._skip(name, t, "drawdown pause active")
        if self.s["daily_halt"]:
            return self._skip(name, t, "daily loss limit hit")
        entry_key = wallet + "|" + t["condition_id"]
        if entry_key in self.s["entries"]:
            return self._skip(name, t, "already copied this trader in this market")
        if any(p["condition_id"] == t["condition_id"] and p["token_id"] != t["token_id"] for p in self.s["positions"]):
            return self._skip(name, t, "we hold the opposite side")

        m = self.api.market(t["condition_id"])
        if not m:
            return self._skip(name, t, "market metadata unavailable")
        tags = self.api.event_tags(t["event_slug"] or ((m.get("events") or [{}])[0].get("slug", "")))
        cat = classify(tags, t["title"] or m.get("question", ""))
        if cat == "sports":
            return self._skip(name, t, "sports")
        if cat not in C.ALLOWED_CATEGORIES:
            return self._skip(name, t, "category not allowed (%s)" % cat)
        if trader["only_categories"] and cat not in trader["only_categories"]:
            return self._skip(name, t, "trader copied for %s only (this is %s)" % ("/".join(trader["only_categories"]), cat))
        if m.get("closed") or m.get("acceptingOrders") is False:
            return self._skip(name, t, "market closed")
        end = _parse_ts(m.get("endDate"))
        if end is None or end - now < C.MIN_TIME_TO_RESOLVE_SEC:
            return self._skip(name, t, "resolves in < 24h" if end else "no end date")
        liq = float(m.get("liquidityNum") or m.get("liquidity") or 0)
        if liq < C.MIN_LIQUIDITY:
            return self._skip(name, t, "liquidity $%.0f < $50k" % liq)

        # sizing (hard limits)
        br = self.bankroll()
        confirmed = self._confirmed(wallet, t)
        size = min(C.PER_TRADE_PCT * br, C.PER_TRADE_MAX)
        if confirmed:
            size = min(size * C.CONFIRM_MULT, C.PER_TRADE_MAX)
        mkt_room = C.PER_MARKET_PCT * br - self.open_cost(t["condition_id"])
        exp_room = C.MAX_EXPOSURE_PCT * br - self.open_cost()
        if exp_room < 1:
            self._limit_hit("max total exposure 40%")
            return self._skip(name, t, "total exposure cap reached")
        if mkt_room < 1:
            self._limit_hit("max per market 5%")
            return self._skip(name, t, "per-market cap reached")
        size = min(size, mkt_room, exp_room)

        try:
            bids, asks = self.api.book(t["token_id"])
        except api.ApiError as e:
            return self._skip(name, t, "order book unavailable")
        shares, avg, spent = simulate_buy(asks, size)
        if shares <= 0:
            return self._skip(name, t, "no asks on book")
        if spent < size * 0.98:
            return self._skip(name, t, "book too thin to fill $%.2f" % size)
        if avg > C.MAX_PRICE:
            return self._skip(name, t, "price %.3f above 0.92" % avg)
        if avg < C.MIN_PRICE:
            return self._skip(name, t, "price %.3f below 0.05" % avg)
        if avg - t["price"] > C.MAX_SLIPPAGE:
            return self._skip(name, t, "our price %.3f > 3c worse than their %.3f" % (avg, t["price"]))

        p = {"id": uuid.uuid4().hex[:8], "wallet": wallet, "trader": name, "condition_id": t["condition_id"],
             "token_id": t["token_id"], "title": t["title"] or m.get("question", ""), "outcome": t["outcome"],
             "category": cat, "shares": shares, "entry_price": avg, "their_price": t["price"], "cost": spent,
             "mark": bids[0][0] if bids else avg, "opened_ts": now, "confirmed": confirmed}
        self.s["positions"].append(p)
        self.s["entries"][entry_key] = now
        self._csv(self.trades_csv, TRADE_COLS, {
            "time_ct": fmt_ct(), "action": "OPEN", "trader": name, "market": p["title"], "side": p["outcome"],
            "our_price": round(avg, 4), "their_price": round(t["price"], 4), "size_usd": round(spent, 2),
            "shares": round(shares, 4), "result": "", "running_pnl": round(self.s["realized_pnl"], 2),
            "note": ("1.5x confirmation, " if confirmed else "") + cat})
        self.s["period"]["copied"].append({"trader": name, "market": p["title"], "side": p["outcome"],
                                           "price": round(avg, 3), "usd": round(spent, 2), "confirmed": confirmed})
        return p

    # ---------------------------------------------------------------- cycle
    def run_cycle(self):
        self.roll_day()
        if self.s["period"]["start_equity"] is None:
            self.s["period"]["start_equity"] = self.equity()
        errors = []
        self.last_fetch = {}
        now = time.time()
        try:
            self.mark_positions()
        except api.ApiError as e:
            errors.append("marking: %s" % e)
        self.check_limits()

        for trader in C.CORE:
            w = trader["wallet"]
            try:
                trades = self.api.recent_trades(w)
            except api.ApiError as e:
                errors.append("%s trades: %s" % (trader["name"], e))
                continue
            newest = max((t["ts"] for t in trades), default=0)
            self.last_fetch[trader["name"]] = (len(trades), int((now - newest) / 60) if newest else None)
            for t in trades:
                if t["side"] == "BUY" and now - t["ts"] <= C.CONFIRM_WINDOW_SEC:
                    self._record_signal(w, t)
            held = None
            if any(p["wallet"] == w for p in self.s["positions"]):
                try:
                    held = self.api.positions(w)
                except api.ApiError as e:
                    errors.append("%s positions: %s" % (trader["name"], e))
            self.handle_exits(w, trades, held)
            for t in sorted(trades, key=lambda x: x["ts"]):
                if t["side"] != "BUY" or not t["tx"] or (t["tx"] + t["token_id"]) in self.s["seen_tx"]:
                    continue
                self.s["seen_tx"][t["tx"] + t["token_id"]] = t["ts"]
                if now - t["ts"] > C.COPY_WINDOW_SEC:
                    continue  # stale: not copied, not logged as a skip
                try:
                    self.evaluate_buy(trader, t)
                except api.ApiError as e:
                    errors.append("%s eval: %s" % (trader["name"], e))
                self.check_limits()

        # housekeeping
        cutoff = now - 3 * 86400
        self.s["seen_tx"] = {k: v for k, v in self.s["seen_tx"].items() if v >= cutoff}
        self.s["signals"] = {k: {w: ts for w, ts in v.items() if ts >= now - C.CONFIRM_WINDOW_SEC}
                             for k, v in self.s["signals"].items()}
        self.s["signals"] = {k: v for k, v in self.s["signals"].items() if v}
        self._api_health(errors)
        self.save()
        return errors

    def _api_health(self, errors):
        if errors:
            self.s["api_fail_streak"] += 1
            # alert on the 2nd consecutive failing cycle, then at most hourly
            if self.s["api_fail_streak"] >= 2 and time.time() - self.s["last_api_alert"] > 3600:
                self.s["last_api_alert"] = time.time()
                self.notify("COPYCAT: API error, %d cycles in a row. %s" % (self.s["api_fail_streak"], errors[0][:300]),
                            urgent=True)
        else:
            self.s["api_fail_streak"] = 0


def _json_list(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except Exception:
        return []


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
