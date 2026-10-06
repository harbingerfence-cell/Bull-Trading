from datetime import datetime, timedelta

from bull_brain.alpaca_data import AlpacaQuoteProvider
from bull_brain.bars import fetch_alpaca_bars, load_csv
from bull_brain.orb import ET, Bar
from bull_brain.sweep import AGGRESSIVE, split_days, sweep
from bull_brain.tests.test_orb import OR, mk


def test_load_csv_aliases(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("t,o,h,l,c,v\n2026-10-06T13:31:00Z,10,11,9,10.5,100\n2026-10-06T13:30:00Z,10,10.5,9.5,10,50\n")
    bars = load_csv(p)
    assert [b.volume for b in bars] == [50, 100] and bars[0].ts < bars[1].ts


def test_fetch_alpaca_bars_paginates():
    pages = [{"bars": [{"t": "2026-10-06T13:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 5}], "next_page_token": "x"},
             {"bars": [{"t": "2026-10-06T13:31:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 6}], "next_page_token": None}]
    seen = []
    def tr(url, h, t):
        seen.append(url)
        return pages[len(seen) - 1]
    p = AlpacaQuoteProvider("k", "s", transport=tr)
    bars = fetch_alpaca_bars(p, "SPY", datetime(2026, 10, 6, tzinfo=ET), datetime(2026, 10, 7, tzinfo=ET))
    assert len(bars) == 2 and "page_token=x" in seen[1] and "/v2/stocks/SPY/bars" in seen[0]


def multi_day(n):
    bars = []
    for d in range(n):
        start = datetime(2026, 9, 1, 9, 30, tzinfo=ET) + timedelta(days=d)
        bars += mk(OR + [100.5, 100.6, 101.0, 101.5, 102.5, 103.0, 103.0], start=start)
    return bars


def test_split_has_no_overlap_and_sweep_reports_both():
    bars = multi_day(10)
    tr, te = split_days(bars)
    assert max(b.ts for b in tr) < min(b.ts for b in te)
    rows = sweep(bars, 25_000, grid={"target_r": [1.0, 2.0]})
    assert len(rows) == 2 and "train" in rows[0] and "test" in rows[0]
    assert AGGRESSIVE.max_trades_per_day == 3
