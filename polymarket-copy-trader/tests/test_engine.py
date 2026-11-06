import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copycat import api, config as C  # noqa: E402
from copycat.engine import Engine  # noqa: E402
from copycat.markets import classify, simulate_buy  # noqa: E402

W = C.CORE[0]["wallet"]
W2 = C.CORE[1]["wallet"]
POL = C.CORE[5]  # politics-only trader


def mk_trade(wallet, side="BUY", price=0.50, age=60, cond="c1", tok="t1", tx=None, title="Will Trump win the election?",
             ev="us-election"):
    return {"wallet": wallet, "ts": int(time.time() - age), "condition_id": cond, "token_id": tok, "side": side,
            "size": 100, "price": price, "tx": tx or "0x%s%s%s" % (wallet[-4:], side, age), "title": title,
            "slug": "", "event_slug": ev, "outcome": "Yes", "type": "TRADE"}


class Fake:
    ApiError = api.ApiError

    def __init__(self):
        self.trades = {}
        self.held = {}
        self.mkt = {}
        self.tags = {"us-election": ["politics", "elections"], "nba": ["sports", "nba"], "btc": ["crypto"]}
        self.books = {}

    def recent_trades(self, w):
        return self.trades.get(w, [])

    def positions(self, w):
        return self.held.get(w, {})

    def closed_positions(self, w):
        return []

    def market(self, c):
        return self.mkt.get(c, {"closed": False, "acceptingOrders": True, "endDate": "2030-01-01T00:00:00Z",
                                "liquidityNum": 200000, "question": "q", "clobTokenIds": '["t1","t2"]',
                                "outcomePrices": '["0.5","0.5"]'})

    def event_tags(self, slug):
        return self.tags.get(slug, [])

    def book(self, tok):
        return self.books.get(tok, ([(0.50, 10000)], [(0.51, 10000)]))


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = Fake()
        self.alerts = []
        self.e = Engine(self.d, client=self.f, notifier=lambda t, urgent=False: self.alerts.append((t, urgent)))

    def test_copies_fresh_buy_at_2pct(self):
        self.f.trades[W] = [mk_trade(W)]
        self.e.run_cycle()
        self.assertEqual(len(self.e.s["positions"]), 1)
        self.assertAlmostEqual(self.e.s["positions"][0]["cost"], 20.0, places=2)

    def test_stale_buy_ignored(self):
        self.f.trades[W] = [mk_trade(W, age=11 * 60)]
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])

    def test_sports_skipped(self):
        self.f.trades[W] = [mk_trade(W, ev="nba", title="Lakers vs Celtics")]
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])
        self.assertEqual(self.e.s["period"]["skipped"][0]["reason"], "sports")

    def test_slippage_price_bounds_liquidity_resolution(self):
        cases = [
            ([(0.50, 1e4)], [(0.54, 1e4)], {}, "worse than"),
            ([(0.92, 1e4)], [(0.93, 1e4)], {}, "above 0.92"),
            ([(0.03, 1e4)], [(0.04, 1e4)], {}, "below 0.05"),
            (None, None, {"liquidityNum": 10000}, "liquidity"),
            (None, None, {"endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))}, "24h"),
        ]
        for i, (bids, asks, mkt, reason) in enumerate(cases):
            f = Fake()
            if bids:
                f.books["t1"] = (bids, asks)
            if mkt:
                base = Fake().market("c1")
                base.update(mkt)
                f.mkt["c1"] = base
            price = 0.04 if reason == "below 0.05" else 0.93 if reason == "above 0.92" else 0.50
            f.trades[W] = [mk_trade(W, price=price)]
            e = Engine(tempfile.mkdtemp(), client=f, notifier=lambda *a, **k: None)
            e.run_cycle()
            self.assertEqual(e.s["positions"], [], reason)
            self.assertIn(reason, e.s["period"]["skipped"][0]["reason"], reason)

    def test_one_entry_per_trader_per_market(self):
        self.f.trades[W] = [mk_trade(W, age=120, tx="a"), mk_trade(W, age=60, tx="b")]
        self.e.run_cycle()
        self.assertEqual(len(self.e.s["positions"]), 1)

    def test_confirmation_boost_and_market_cap(self):
        self.f.trades[W] = [mk_trade(W, age=120, tx="a")]
        self.f.trades[W2] = [mk_trade(W2, age=60, tx="b")]
        self.f.held = {W: {"t1": 100}, W2: {"t1": 100}}
        self.e.run_cycle()
        costs = sorted(round(p["cost"], 2) for p in self.e.s["positions"])
        self.assertEqual(costs, [20.0, 30.0])  # second is 1.5x; total = 5% market cap
        W3 = C.CORE[2]["wallet"]
        self.f.trades[W3] = [mk_trade(W3, age=30, tx="c")]
        self.e.run_cycle()
        self.assertEqual(len(self.e.s["positions"]), 2)
        self.assertIn("per-market cap", self.e.s["period"]["skipped"][-1]["reason"])

    def test_politics_only_trader(self):
        w = POL["wallet"]
        self.f.trades[w] = [mk_trade(w, ev="btc", title="Bitcoin above 100k?")]
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])
        self.assertIn("politics only", self.e.s["period"]["skipped"][0]["reason"])

    def test_exit_when_trader_sells(self):
        self.f.trades[W] = [mk_trade(W, age=60, tx="a")]
        self.f.held[W] = {"t1": 100}
        self.e.run_cycle()
        self.f.trades[W].append(mk_trade(W, side="SELL", age=0, tx="s", price=0.60))
        self.f.books["t1"] = ([(0.60, 1e4)], [(0.61, 1e4)])
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])
        self.assertGreater(self.e.s["realized_pnl"], 0)

    def test_three_losses_demotes(self):
        for i in range(3):
            c, tok = "c%d" % i, "tk%d" % i
            self.f.mkt[c] = dict(Fake().market(c), clobTokenIds='["%s","x"]' % tok)
            self.f.trades[W] = [mk_trade(W, age=60, tx="b%d" % i, cond=c, tok=tok)]
            self.f.held[W] = {tok: 100}
            self.e.run_cycle()
            self.f.trades[W].append(mk_trade(W, side="SELL", age=0, tx="s%d" % i, cond=c, tok=tok, price=0.4))
            self.f.books[tok] = ([(0.40, 1e4)], [(0.41, 1e4)])
            self.e.run_cycle()
        self.assertIn(W, self.e.s["demoted"])
        self.f.trades[W] = [mk_trade(W, age=30, tx="new", cond="c9", tok="t1")]
        self.e.run_cycle()
        self.assertFalse(any(p["condition_id"] == "c9" for p in self.e.s["positions"]))

    def test_daily_loss_halt_alerts(self):
        self.f.trades[W] = [mk_trade(W, age=60, tx="a")]
        self.e.run_cycle()
        self.e.s["realized_pnl"] = -60  # simulate a bad day
        self.e.check_limits()
        self.assertTrue(self.e.s["daily_halt"])
        self.assertTrue(any(u for _, u in self.alerts))

    def test_drawdown_pause(self):
        self.e.s["peak_equity"] = 1200
        self.e.s["realized_pnl"] = -10
        self.e.check_limits()
        self.assertTrue(self.e.s["dd_paused"])
        self.f.trades[W] = [mk_trade(W)]
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])

    def test_exposure_cap(self):
        for i in range(25):
            c, tok = "m%d" % i, "k%d" % i
            self.f.mkt[c] = dict(Fake().market(c), clobTokenIds='["%s","x"]' % tok)
        self.f.trades[W] = [mk_trade(W, age=100 + i, tx="x%d" % i, cond="m%d" % i, tok="k%d" % i) for i in range(25)]
        self.e.run_cycle()
        self.assertLessEqual(self.e.open_cost(), 400.0 + 1e-6)
        self.assertIn("max total exposure 40%", self.e.s["period"]["limit_hits"])

    def test_exit_when_position_gone(self):
        self.f.trades[W] = [mk_trade(W)]
        self.e.run_cycle()
        self.e.run_cycle()  # positions API lag: not closed yet
        self.assertEqual(len(self.e.s["positions"]), 1)
        self.e.s["positions"][0]["opened_ts"] -= 16 * 60
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])

    def test_settlement(self):
        self.f.trades[W] = [mk_trade(W)]
        self.e.run_cycle()
        self.f.mkt["c1"] = dict(Fake().market("c1"), closed=True, outcomePrices='["1","0"]')
        self.e.run_cycle()
        self.assertEqual(self.e.s["positions"], [])
        self.assertGreater(self.e.s["realized_pnl"], 15)

    def test_classify(self):
        self.assertEqual(classify(["sports"], "x"), "sports")
        self.assertEqual(classify([], "Will the Fed cut rates in December?"), "economics")
        self.assertEqual(classify([], "Lakers vs Celtics NBA"), "sports")
        s, avg, spent = simulate_buy([(0.5, 10), (0.6, 100)], 20)
        self.assertAlmostEqual(spent, 20)
        self.assertGreater(avg, 0.5)


if __name__ == "__main__":
    unittest.main()
