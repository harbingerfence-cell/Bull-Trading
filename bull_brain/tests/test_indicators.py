import pytest

from bull_brain.indicators import atr, ema, relative_volume, rsi, session_vwap, sma
from bull_brain.orb import ET, Bar
from datetime import datetime, timedelta


def test_sma_ema():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    e = ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2 and e[3] == pytest.approx(3.0) and e[4] == pytest.approx(4.0)
    assert ema([1, 2], 3) == [None, None]


def test_rsi_extremes_and_warmup():
    assert rsi(list(range(1, 30)), 14)[-1] == 100.0
    assert rsi(list(range(30, 1, -1)), 14)[-1] == pytest.approx(0.0)
    r = rsi([1, 2, 1, 2] * 10, 14)
    assert r[:14] == [None] * 14 and 40 < r[-1] < 60
    assert rsi([1, 2, 3], 14) == [None] * 3


def mk(highs_lows_closes):
    t0 = datetime(2026, 10, 6, 9, 30, tzinfo=ET)
    return [Bar(t0 + timedelta(minutes=i), c, h, l, c, 100) for i, (h, l, c) in enumerate(highs_lows_closes)]


def test_atr_and_vwap_and_relvol():
    bars = mk([(11, 9, 10)] * 20)
    a = atr(bars, 14)
    assert a[13] is None and a[14] == pytest.approx(2.0) and a[-1] == pytest.approx(2.0)
    v = session_vwap(mk([(11, 9, 10), (13, 11, 12)]))
    assert v[0] == pytest.approx(10) and v[1] == pytest.approx(11)
    assert relative_volume(300, [100, 100, 100]) == 3.0 and relative_volume(1, []) is None
