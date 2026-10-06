from datetime import datetime, timezone

import pytest

from bull_brain.alpaca_data import AlpacaError, AlpacaQuoteProvider, parse_timestamp
from bull_brain.market_data import MarketDataAdapter, QuoteStatus
from bull_brain.tests.test_acceptance import NOW, full_limits

TS = "2026-10-06T14:59:58.123456789Z"


def prov(body, **kw):
    calls = []
    def transport(url, headers, timeout):
        calls.append((url, headers))
        if isinstance(body, Exception):
            raise body
        return body
    p = AlpacaQuoteProvider("k", "s", transport=transport, **kw)
    p.calls = calls
    return p


def test_stock_quote_parsed_with_nanosecond_timestamp():
    p = prov({"symbol": "XYZ", "quote": {"t": TS, "bp": 99.95, "ap": 100.05}})
    q = p.get_quote("XYZ", "TEST")
    assert (q.bid, q.ask, q.source, q.delayed) == (99.95, 100.05, "alpaca:iex", False)
    assert q.timestamp == datetime(2026, 10, 6, 14, 59, 58, 123456, tzinfo=timezone.utc)
    url, headers = p.calls[0]
    assert "/v2/stocks/XYZ/quotes/latest" in url and "feed=iex" in url
    assert headers["APCA-API-KEY-ID"] == "k"


def test_crypto_and_option_routing():
    c = prov({"quotes": {"BTC/USD": {"t": TS, "bp": 60000, "ap": 60010}}})
    assert c.get_quote("BTC/USD", "ALPACA").source == "alpaca:crypto"
    assert "crypto/us/latest/quotes" in c.calls[0][0]
    o = prov({"quotes": {"AAPL261120C00200000": {"t": TS, "bp": 1.0, "ap": 1.1}}})
    q = o.get_quote("AAPL261120C00200000", "OPRA")
    assert q.source == "alpaca:options:indicative" and "options/quotes/latest" in o.calls[0][0]


def test_zero_side_or_empty_means_no_quote():
    assert prov({"quote": {"t": TS, "bp": 0, "ap": 100.0}}).get_quote("XYZ", "T") is None
    assert prov({"quotes": {}}).get_quote("BTC/USD", "A") is None


def test_delayed_feed_flagged():
    p = prov({"quote": {"t": TS, "bp": 1, "ap": 2}}, equity_feed="delayed_sip")
    assert p.get_quote("XYZ", "T").delayed


def test_missing_credentials_and_no_secret_in_repr(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(AlpacaError):
        AlpacaQuoteProvider()
    assert "secret" not in repr(prov({}))  # key/secret never in repr


def test_http_error_becomes_adapter_error_and_needs_data():
    a = MarketDataAdapter(prov(AlpacaError("HTTP 403 from Alpaca data API")), full_limits())
    q, chk = a.fetch("XYZ", "T", NOW)
    assert q is None and chk.status is QuoteStatus.ERROR and "403" in chk.detail


def test_end_to_end_fresh_via_adapter():
    body = {"quote": {"t": "2026-10-06T14:59:58Z", "bp": 99.95, "ap": 100.05}}
    a = MarketDataAdapter(prov(body), full_limits())
    assert a.fetch("XYZ", "T", NOW)[1].status is QuoteStatus.FRESH


def test_non_alpaca_host_refused():
    from bull_brain.alpaca_data import _urllib_transport
    with pytest.raises(AlpacaError):
        _urllib_transport("https://evil.example/x", {}, 1)
