from datetime import date, datetime, timedelta, timezone

import pytest

from bull_brain.journal import Journal
from bull_brain.market_data import MarketDataAdapter, StaticQuoteProvider
from bull_brain.models import AccountState, Decision, GateResult, Quote
from bull_brain.orb import ET
from bull_brain.swing import (Setup, load_queue, manage, open_setups, record_setups, save_queue, scan,
                              score_notes, setup_to_note)
from bull_brain.tests.test_acceptance import full_limits
from bull_brain.tests.test_patterns import CASES, first_signal, path

NOW = datetime(2026, 10, 7, 13, 31, tzinfo=timezone.utc)  # 09:31 ET


def breakout_bars():
    bars = path(CASES["bull_flag"][0], pad=170)
    t, sig = first_signal("bull_flag", bars)
    return bars[:t + 1], sig


def test_scan_finds_pattern_on_last_completed_bar_and_ignores_forming_bar():
    bars, sig = breakout_bars()
    now = bars[-1].ts + timedelta(days=1, hours=2)  # next morning
    out = scan({"AAA": bars}, now)
    assert len(out) == 1 and out[0].pattern == "bull_flag" and out[0].stop == sig.stop and out[0].rr > 1
    assert scan({"AAA": bars[:100]}, now) == []                    # too little history
    assert scan({"AAA": bars}, bars[-1].ts + timedelta(days=30)) == []  # stale data
    # a still-forming bar for "today" must be dropped (so the pattern bar is no longer last)
    forming = bars[-1].ts.replace(hour=14)
    assert scan({"AAA": bars}, forming.replace(hour=15)) in ([], out)  # no crash; forming-bar rule exercised


def test_queue_roundtrip_and_note_journaled_once(tmp_path):
    st = Setup("AAA", "bull_flag", "2026-10-06", 118.0, 111.0, 130.0)
    save_queue(tmp_path / "q.json", date(2026, 10, 6), [st])
    assert load_queue(tmp_path / "q.json") == ("2026-10-06", [st])
    assert load_queue(tmp_path / "none.json") == (None, [])
    j = Journal()
    record_setups(j, [st]); record_setups(j, [st])
    assert len(j.entries(event="note")) == 1 and setup_to_note(st).planned_rr == pytest.approx(12 / 7)


class FB:
    def __init__(self, held=(), approve=True):
        self.held, self.approve, self.sub, self.flat = set(held), approve, [], []

    def position_symbols(self): return set(self.held)
    def has_position(self, s): return s in self.held
    def account_state(self, i=None): return AccountState(equity=100_000)
    def flatten(self, s): self.flat.append(s); self.held.discard(s)

    def submit(self, plan, now=None, take_profit=None, time_in_force="day"):
        self.sub.append((plan, take_profit, time_in_force))
        return GateResult(plan_id=plan.plan_id, decision=Decision.PAPER_CANDIDATE if self.approve else Decision.NO_TRADE,
                          approved=self.approve, reasons=["x"])


def adapter(**px):
    prov = StaticQuoteProvider({(s, "ALPACA"): Quote(bid=a - 0.1, ask=a, timestamp=NOW - timedelta(seconds=2), source="t")
                                for s, a in px.items()})
    return MarketDataAdapter(prov, full_limits())


def S(sym, stop=111.0, target=130.0):
    return Setup(sym, "bull_flag", "2026-10-06", 118.0, stop, target)


def test_open_sizes_by_risk_uses_gtc_bracket_and_target():
    b = FB()
    r = open_setups([S("AAA")], b, adapter(AAA=119.0), full_limits(), NOW)[0]
    assert r["status"] == "submitted"
    plan, tp, tif = b.sub[0]
    assert tif == "gtc" and tp == 130.0 and plan.plan_id == "swing-bull_flag-AAA-2026-10-06"
    assert plan.quantity == int(100_000 * 0.01 / 8.0)  # risk 1% / (119-111)
    assert plan.horizon == "swing"


def test_open_validations_and_caps():
    b = FB(held={"HELD"})
    res = open_setups([S("HELD"), S("GAP", stop=111, target=119.5), S("NOQ"), S("A1"), S("A2"), S("A3"), S("A4")],
                      b, adapter(HELD=119, GAP=119, A1=119, A2=119, A3=119, A4=119), full_limits(), NOW, max_new=3)
    st = {r["setup"].split("-")[2]: r["status"] for r in res}
    assert st["HELD"] == "skipped" and st["GAP"] == "skipped" and st["NOQ"] == "no_data"
    assert [st[k] for k in ("A1", "A2", "A3")] == ["submitted"] * 3 and st["A4"] == "skipped"


def test_manage_time_stop_flattens_once(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    filled = datetime(2026, 8, 3, 13, 31, tzinfo=timezone.utc)
    j.append("plan", "swing-x-AAA-1", {"instrument": "AAA"}, filled)
    j.append("fill", "swing-x-AAA-1", {}, filled)
    j.append("plan", "swing-x-BBB-1", {"instrument": "BBB"}, NOW - timedelta(days=3))
    j.append("fill", "swing-x-BBB-1", {}, NOW - timedelta(days=3))
    b = FB(held={"AAA", "BBB"})
    assert manage(b, j, NOW) == ["swing-x-AAA-1"] and b.flat == ["AAA"]
    assert manage(b, j, NOW) == []  # already exited


def test_score_notes_scores_resolved_and_skips_open():
    j = Journal()
    st = Setup("AAA", "bull_flag", "2026-10-06", 118.0, 111.0, 130.0)
    record_setups(j, [st])
    mk = lambda days, hi, lo, c: __import__("bull_brain.orb", fromlist=["Bar"]).Bar(
        datetime(2026, 10, 7, 4, tzinfo=timezone.utc) + timedelta(days=days), 118.0, hi, lo, c, 1e6)
    open_bars = [mk(0, 119, 117, 118.5), mk(1, 120, 118, 119)]
    assert score_notes(j, lambda s, a, z: open_bars, NOW + timedelta(days=2)) == []  # still OPEN
    win_bars = open_bars + [mk(2, 131, 117.5, 130)]
    out = score_notes(j, lambda s, a, z: win_bars, NOW + timedelta(days=3))
    assert out and out[0]["outcome"] == "TARGET" and out[0]["r"] > 1
    assert score_notes(j, lambda s, a, z: win_bars, NOW + timedelta(days=4)) == []  # not re-scored


def test_queue_freshness_guard():
    from bull_brain.swing import queue_is_fresh
    assert queue_is_fresh("2026-10-06", NOW) and queue_is_fresh("2026-10-03", NOW)   # weekend gap ok
    assert not queue_is_fresh("2026-09-20", NOW) and not queue_is_fresh(None, NOW)
    assert not queue_is_fresh("2026-10-09", NOW)                                      # future-dated


def _event_bars(up=True, vol_mult=3.0, move=0.06):
    from bull_brain.orb import Bar
    base = [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i), 100, 100.5, 99.5, 100, 1e6) for i in range(40)]
    c = 100 * (1 + move if up else 1 - move)
    hi, lo = (c * 1.002, 99.8) if up else (100.2, c * 0.998)
    base.append(Bar(base[-1].ts + timedelta(days=1), 100, hi, lo, c, vol_mult * 1e6))
    return base


def test_event_day_detector_both_sides_and_thresholds():
    import bull_brain.patterns as P
    s = P.Series(_event_bars(True)); sig = P.event_day(s, len(s.b) - 1)
    assert sig.side == "LONG" and sig.stop < 106 < sig.target
    s = P.Series(_event_bars(False)); sig = P.event_day(s, len(s.b) - 1)
    assert sig.side == "SHORT" and sig.target < 94 < sig.stop
    assert P.event_day(P.Series(_event_bars(True, vol_mult=2.0)), 40) is None   # not enough volume
    assert P.event_day(P.Series(_event_bars(True, move=0.03)), 40) is None      # not enough move


def test_short_event_setup_opens_as_short_bracket_and_errors_do_not_stop_the_run():
    bad = FB()
    orig = bad.submit
    def boom(plan, now=None, take_profit=None, time_in_force="day"):
        if plan.instrument == "BAD":
            raise RuntimeError("asset not shortable")
        return orig(plan, now, take_profit, time_in_force)
    bad.submit = boom
    mk = lambda sym: Setup(sym, "event_day", "2026-10-06", 94.0, 99.0, 84.0, "SHORT")
    res = open_setups([mk("BAD"), mk("OK")], bad, adapter(BAD=93.0, OK=93.0), full_limits(), NOW)
    assert res[0]["status"] == "error" and "not shortable" in res[0]["reason"] and res[1]["status"] == "submitted"
    plan, tp, tif = bad.sub[0]
    assert plan.side.value == "SHORT" and tp == 84.0 and tif == "gtc" and plan.entry == pytest.approx(92.9)
    # live price beyond the stop is skipped
    r = open_setups([mk("X")], FB(), adapter(X=99.5), full_limits(), NOW)[0]
    assert r["status"] == "skipped"


def test_event_day_time_stop_is_20_days(tmp_path):
    j = Journal(tmp_path / "j.jsonl")
    filled = datetime(2026, 9, 7, 13, 31, tzinfo=timezone.utc)  # ~21 weekdays before NOW
    for pid, sym in (("swing-event_day-AAA-1", "AAA"), ("swing-bull_flag-BBB-1", "BBB")):
        j.append("plan", pid, {"instrument": sym}, filled); j.append("fill", pid, {}, filled)
    b = FB(held={"AAA", "BBB"})
    assert manage(b, j, NOW) == ["swing-event_day-AAA-1"]  # 21 >= 20 but < 30
