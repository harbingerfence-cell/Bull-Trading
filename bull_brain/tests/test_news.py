import pytest

from bull_brain.news import CATEGORIES, classify

CASES = [
    ("Baird Maintains Outperform on NVIDIA, Raises Price Target to $1200", "pt_raise", "LONG"),
    ("Stifel Maintains Buy on Tesla, Lowers Price Target to $200", "pt_cut", "SHORT"),
    ("Morgan Stanley Upgrades Intel to Overweight, Raises Price Target to $40", "upgrade", "LONG"),
    ("Goldman Downgrades Nike to Sell, Lowers Price Target to $70", "downgrade", "SHORT"),
    ("JPMorgan Initiates Coverage On Palantir with Overweight Rating, Announces Price Target of $30", "initiate_buy", "LONG"),
    ("BofA Initiates Coverage On Peloton with Underperform Rating", "initiate_sell", "SHORT"),
    ("Apple Q4 EPS $1.46 Beats $1.39 Estimate, Sales $89.5B Miss $89.9B Estimate", "eps_beat", "LONG"),
    ("Intel Q2 EPS $0.02 Misses $0.10 Estimate", "eps_miss", "SHORT"),
    ("Salesforce Raises FY25 Revenue Guidance", "guidance_raise", "LONG"),
    ("Nike Lowers FY Revenue Outlook", "guidance_cut", "SHORT"),
    ("Company Announces $5B Share Buyback Program", "buyback", "LONG"),
    ("Company Announces Pricing Of Public Offering Of Common Stock", "offering", "SHORT"),
]


@pytest.mark.parametrize("headline,cat,direction", CASES)
def test_classification(headline, cat, direction):
    assert classify(headline, ["XYZ"]) == (cat, direction)


def test_multi_symbol_and_unrelated_headlines_ignored():
    assert classify("Baird Maintains Outperform on NVIDIA, Raises Price Target to $1200", ["NVDA", "AMD"]) is None
    assert classify("Investment Advisor Says Throwing Darts Would Beat ARKK", ["ARKK"]) is None
    assert classify("Top 10 Trending Stocks On WallStreetBets", []) is None


def test_downgrade_wins_over_price_target_and_all_categories_reachable():
    assert classify("X Downgrades Y to Neutral, Raises Price Target to $9", ["Y"])[0] == "downgrade"
    assert set(CATEGORIES) == {c for _, c, _ in CASES}


def test_eps_beat_with_sales_miss_is_still_a_beat_and_vice_versa():
    assert classify("Co Q1 EPS $2 Beats $1.5 Estimate, Sales $9B Miss $10B Estimate", ["Z"])[0] == "eps_beat"
    assert classify("Co Q1 EPS $1 Misses $1.5 Estimate, Sales $11B Beat $10B Estimate", ["Z"])[0] == "eps_miss"
