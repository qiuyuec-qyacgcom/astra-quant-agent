"""币安行情接入（factor 引擎按 venue 分流）的单元测试。

覆盖：
1. `market_normalizer` 的盘口/ticker/OI 归一化（币安 → OKX 口径）。
2. `market_data_service` 各 fetch 函数的 venue 路由：
   - venue=None/auto/okx → 保持原 OKX 行为（不触达币安）。
   - venue=binance → 走 BinanceExchange 公开方法，且失败 fail-closed 返回 None/空。
3. 币安模式指标走本地蜡烛兜底且 venue 透传（严禁跨所用 OKX 蜡烛冒充）。
"""
import unittest
from unittest.mock import patch, MagicMock

from scripts.market_data_service import (
    fetch_ticker,
    fetch_tickers_bulk,
    fetch_orderbook_depth,
    fetch_indicators_batch,
    fetch_single_indicator,
    fetch_candles,
    fetch_funding_rate,
    fetch_open_interest,
    _local_math_indicators,
)
from scripts.market_normalizer import (
    normalize_venue,
    normalize_depth,
    normalize_ticker,
    normalize_oi,
)


class TestNormalizeVenue(unittest.TestCase):
    def test_none_auto_okx_map_to_default(self):
        self.assertIsNone(normalize_venue(None))
        self.assertIsNone(normalize_venue("auto"))
        self.assertIsNone(normalize_venue("okx"))
        self.assertIsNone(normalize_venue("OKX"))
        self.assertIsNone(normalize_venue(""))

    def test_binance_lowercased(self):
        self.assertEqual(normalize_venue("binance"), "binance")
        self.assertEqual(normalize_venue("BINANCE"), "binance")


class TestNormalizeDepth(unittest.TestCase):
    def test_binance_appends_numorders(self):
        raw = {"bids": [["1.0", "5"], ["2.0", "3"]], "asks": [["3.0", "4"]]}
        out = normalize_depth(raw, venue="binance")
        self.assertEqual(out["bids"], [["1.0", "5", 1], ["2.0", "3", 1]])
        self.assertEqual(out["asks"], [["3.0", "4", 1]])

    def test_okx_preserves_numorders(self):
        raw = {"bids": [["1.0", "5", "7"]], "asks": [["3.0", "4", "9"]]}
        out = normalize_depth(raw, venue="okx")
        self.assertEqual(out["bids"], [["1.0", "5", "7"]])
        self.assertEqual(out["asks"], [["3.0", "4", "9"]])

    def test_none_input_returns_none(self):
        self.assertIsNone(normalize_depth(None, venue="binance"))
        self.assertEqual(normalize_depth({"bids": None, "asks": None}, venue="binance"),
                         {"bids": [], "asks": []})


class TestNormalizeTickerOi(unittest.TestCase):
    def test_ticker_defaults_missing_fields(self):
        out = normalize_ticker({"last": "10", "bidPx": "9"}, venue="binance")
        self.assertEqual(out["last"], "10")
        self.assertEqual(out["bidPx"], "9")
        self.assertEqual(out["open24h"], "0")

    def test_oi_normalized(self):
        out = normalize_oi({"oi": "1", "oiCcy": "2", "oiUsd": "3", "ts": "4"})
        self.assertEqual(out, {"oi": "1", "oiCcy": "2", "oiUsd": "3", "ts": "4"})


class TestVenueRouting(unittest.TestCase):
    def _fake_binance(self):
        ex = MagicMock()
        ex.ticker.return_value = {"instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99",
                                  "askPx": "101", "open24h": "90", "high24h": "105",
                                  "low24h": "85", "vol24h": "10", "volCcy24h": "1000", "ts": "1"}
        ex.tickers.return_value = {"BTC-USDT-SWAP": {"last": "100"}}
        ex.orderbook.return_value = {"bids": [["99", "5"]], "asks": [["101", "4"]]}
        ex.candles.return_value = [[str(1700000000000), "1", "2", "0.5", "1.5", "10", "15", "15", "1"]]
        ex.funding_rate.return_value = 0.01
        ex.open_interest.return_value = {"oi": "1", "oiCcy": "1", "oiUsd": "100", "ts": "1"}
        return ex

    def test_binance_ticker_routes(self):
        with patch("scripts.market_data_service._binance_exchange", return_value=self._fake_binance()):
            out = fetch_ticker("BTC-USDT-SWAP", venue="binance")
            self.assertEqual(out["last"], "100")

    def test_binance_orderbook_normalizes(self):
        with patch("scripts.market_data_service._binance_exchange", return_value=self._fake_binance()):
            out = fetch_orderbook_depth("BTC-USDT-SWAP", sz=5, venue="binance")
            self.assertEqual(out["bids"], [["99", "5", 1]])

    def test_binance_funding_rate(self):
        with patch("scripts.market_data_service._binance_exchange", return_value=self._fake_binance()):
            self.assertEqual(fetch_funding_rate("BTC-USDT-SWAP", venue="binance"), 0.01)

    def test_binance_open_interest_normalizes(self):
        with patch("scripts.market_data_service._binance_exchange", return_value=self._fake_binance()):
            out = fetch_open_interest("BTC-USDT-SWAP", venue="binance")
            self.assertEqual(out["oiUsd"], "100")

    def test_binance_candles_routes(self):
        with patch("scripts.market_data_service._binance_exchange", return_value=self._fake_binance()):
            out = fetch_candles("BTC-USDT-SWAP", bar="15m", limit=24, venue="binance")
            self.assertEqual(len(out), 1)

    def test_binance_failure_fails_closed(self):
        ex = MagicMock()
        ex.ticker.side_effect = RuntimeError("boom")
        ex.tickers.side_effect = RuntimeError("boom")
        ex.candles.side_effect = RuntimeError("boom")
        with patch("scripts.market_data_service._binance_exchange", return_value=ex):
            self.assertIsNone(fetch_ticker("BTC-USDT-SWAP", venue="binance"))
            self.assertEqual(fetch_tickers_bulk(venue="binance"), {})
            self.assertEqual(fetch_candles("BTC-USDT-SWAP", venue="binance"), [])

    def test_default_venue_does_not_touch_binance(self):
        # venue 未指定时，必须走 OKX 路径，不调用 _binance_exchange
        with patch("scripts.market_data_service._binance_exchange") as m, \
             patch("scripts.market_data_service._public_get", return_value=None):
            fetch_ticker("BTC-USDT-SWAP")
            m.assert_not_called()


class TestBinanceIndicatorsLocalFallback(unittest.TestCase):
    def test_indicators_batch_binance_uses_local_candles_with_venue(self):
        with patch("scripts.market_data_service.fetch_candles", return_value=[]) as fc:
            out = fetch_indicators_batch("BTC-USDT-SWAP", ["adx"], bar="1H", venue="binance")
            self.assertEqual(out, {})
            fc.assert_called_once()
            self.assertEqual(fc.call_args.kwargs.get("venue"), "binance")

    def test_single_indicator_binance_uses_local(self):
        with patch("scripts.market_data_service.fetch_candles", return_value=[]):
            out = fetch_single_indicator("BTC-USDT-SWAP", "adx", bar="1H", venue="binance")
            self.assertEqual(out, {})

    def test_local_math_indicators_passes_venue(self):
        with patch("scripts.market_data_service.fetch_candles", return_value=[]) as fc:
            _local_math_indicators("BTC-USDT-SWAP", ["adx"], bar="1H", venue="binance")
            self.assertEqual(fc.call_args.kwargs.get("venue"), "binance")


if __name__ == "__main__":
    unittest.main()
