"""币安 WebSocket 行情：符号映射 / 订阅帧 / 帧解析 / 状态快照 / 常驻采集。

覆盖改造的三个关键契约：
1. **符号与订阅**：规范标的 → 币安原生流名（小写无分隔），一帧订阅拿 ticker+三档K线；
2. **帧解析**：`24hrTicker` / `kline` / combined 三种形态都归一，解析失败只留痕不抛；
3. **快照与降级**：`MarketState` 有界 + newest-first 9 列；`market_data_service`
   优先读快照、**过期/不足则回退 REST**（fail-open，绝不因流断就没数据）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import market_stream as ms  # noqa: E402
import market_data_service as mds  # noqa: E402


class SymbolAndSubscribeTests(unittest.TestCase):
    def test_venue_symbol_is_binance_native_lowercase(self):
        self.assertEqual(ms.venue_symbol("binance", "BTC-USDT-SWAP"), "btcusdt")
        self.assertEqual(ms.venue_symbol("binance", "1000PEPE-USDT-SWAP"), "1000pepeusdt")
        # OKX 行为不变
        self.assertEqual(ms.venue_symbol("okx", "BTC-USDT-SWAP"), "BTC-USDT-SWAP")

    def test_subscribe_payload_covers_ticker_and_three_klines(self):
        payload = ms.subscribe_payload("binance", "btcusdt")
        self.assertEqual(payload["method"], "SUBSCRIBE")
        params = payload["params"]
        self.assertIn("btcusdt@ticker", params)
        for suffix in ("15m", "1h", "4h"):
            self.assertIn(f"btcusdt@kline_{suffix}", params)

    def test_endpoint_is_binance_usdm_ws(self):
        self.assertTrue(ms.VENUE_ENDPOINTS["binance"].startswith("wss://fstream.binance.com"))


class ParseFrameTests(unittest.TestCase):
    def _tick(self, payload, now_ms=1_700_000_000_000):
        return ms.parse_frame("binance", json.dumps(payload), now_ms=now_ms)

    def test_ticker_frame_is_normalised(self):
        out = self._tick({"e": "24hrTicker", "s": "BTCUSDT", "c": "80722.10",
                          "b": "80722.00", "a": "80722.20", "o": "80000",
                          "v": "12345.6", "E": 1_699_999_999_500})
        self.assertIsNone(out["error"])
        self.assertEqual(len(out["ticks"]), 1)
        tick = out["ticks"][0]
        self.assertEqual(tick["symbol"], "BTC-USDT-SWAP")
        self.assertAlmostEqual(tick["price"], 80722.10)
        self.assertAlmostEqual(tick["bid"], 80722.00)
        self.assertAlmostEqual(tick["ask"], 80722.20)
        self.assertAlmostEqual(tick["open24h"], 80000.0)
        self.assertEqual(tick["latency_ms"], 500)

    def test_kline_frame_is_normalised_and_maps_interval(self):
        out = self._tick({"e": "kline", "E": 1_700_000_000_000, "k": {
            "s": "ETHUSDT", "i": "1h", "t": 1_699_996_800_000,
            "o": "3000", "h": "3050", "l": "2990", "c": "3040",
            "v": "100", "q": "304000", "x": True}})
        self.assertIsNone(out["error"])
        row = out["ticks"][0]
        self.assertEqual(row["kind"], "kline")
        self.assertEqual(row["symbol"], "ETH-USDT-SWAP")
        self.assertEqual(row["bar"], "1H", "币安 1h 必须映射回内部 1H")
        self.assertTrue(row["closed"])
        self.assertAlmostEqual(row["close"], 3040.0)

    def test_combined_stream_frame_unwraps_data(self):
        out = self._tick({"stream": "btcusdt@ticker",
                          "data": {"e": "24hrTicker", "s": "BTCUSDT", "c": "1"}})
        self.assertEqual(out["ticks"][0]["symbol"], "BTC-USDT-SWAP")

    def test_subscribe_ack_is_control_not_error(self):
        out = self._tick({"result": None, "id": 1})
        self.assertEqual(out["ticks"], [])
        self.assertIsNone(out["error"])
        self.assertIn("subscribed", out["control"] or "")

    def test_error_frame_is_reported(self):
        out = self._tick({"code": -1121, "msg": "Invalid symbol."})
        self.assertIn("Invalid symbol", out["error"] or "")

    def test_garbage_frame_never_raises(self):
        out = ms.parse_frame("binance", "{not json")
        self.assertEqual(out["ticks"], [])
        self.assertIsNotNone(out["error"])


class MarketStateTests(unittest.TestCase):
    def test_kline_upsert_and_newest_first(self):
        state = ms.MarketState(max_bars=3)
        for i, (t, c) in enumerate([(1000, "1"), (2000, "2"), (3000, "3")]):
            state.update({"kind": "kline", "symbol": "BTC-USDT-SWAP", "bar": "15m",
                          "open_ms": t, "open": "1", "high": "1", "low": "1",
                          "close": c, "vol": "1", "quote_vol": "1", "closed": True})
        rows = state.candles("BTC-USDT-SWAP", "15m", 10)
        self.assertEqual([r[4] for r in rows], ["3", "2", "1"], "必须 newest-first")
        self.assertEqual(len(rows[0]), 9, "9 列，确认列在 index 8")

    def test_state_is_bounded(self):
        state = ms.MarketState(max_bars=2)
        for t in (1000, 2000, 3000):
            state.update({"kind": "kline", "symbol": "BTC-USDT-SWAP", "bar": "15m",
                          "open_ms": t, "open": "1", "high": "1", "low": "1",
                          "close": "1", "vol": "1", "quote_vol": "1", "closed": True})
        self.assertEqual(len(state.candles("BTC-USDT-SWAP", "15m", 10)), 2)

    def test_ticker_overwrites(self):
        state = ms.MarketState()
        state.update({"kind": "ticker", "symbol": "BTC-USDT-SWAP", "price": 1.0})
        state.update({"kind": "ticker", "symbol": "BTC-USDT-SWAP", "price": 2.0, "bid": 1.9})
        self.assertAlmostEqual(state.ticker("BTC-USDT-SWAP")["last"], 2.0)

    def test_snapshot_roundtrip_through_disk(self):
        state = ms.MarketState()
        state.update({"kind": "ticker", "symbol": "BTC-USDT-SWAP", "price": 100.0,
                      "bid": 99.9, "ask": 100.1})
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ws.json")
            self.assertTrue(ms.write_snapshot(path, state.snapshot(now_ms=1_700_000_000_000)))
            loaded = ms.load_market_snapshot(path)
            self.assertEqual(loaded["schema_version"], ms.MARKET_SCHEMA_VERSION)
            self.assertAlmostEqual(loaded["tickers"]["BTC-USDT-SWAP"]["bid"], 99.9)

    def test_version_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ws.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"schema_version": 999, "tickers": {}}, fh)
            self.assertEqual(ms.load_market_snapshot(path), {})


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, payload):
        self.sent.append(payload)

    def recv(self, timeout=None):
        if self._frames:
            return self._frames.pop(0)
        raise ConnectionError("fake stream done")


class RunStreamTests(unittest.TestCase):
    def test_run_stream_consumes_frames_and_writes_snapshot(self):
        frames = [
            json.dumps({"result": None, "id": 1}),
            json.dumps({"e": "24hrTicker", "s": "BTCUSDT", "c": "100", "b": "99", "a": "101"}),
            json.dumps({"e": "kline", "k": {"s": "BTCUSDT", "i": "15m", "t": 1000,
                                            "o": "1", "h": "2", "l": "1", "c": "2",
                                            "v": "1", "q": "2", "x": True}}),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ws.json")
            snap = ms.run_stream(
                venue="binance", symbols=["BTC-USDT-SWAP"], snapshot_path=path,
                connect_factory=lambda url, **kw: _FakeWS(frames),
                max_rounds=1, reconnect_backoff=0.01, flush_seconds=0.0,
                now=lambda: 1_700_000_000_000)
            self.assertIn("BTC-USDT-SWAP", snap["tickers"])
            self.assertEqual(len(snap["klines"]["BTC-USDT-SWAP"]["15m"]), 1)
            self.assertTrue(os.path.exists(path), "快照必须落盘")
            self.assertGreaterEqual(snap["health"]["venues"]["binance"]["frames"], 1)


class MarketDataServicePrefersSnapshotTests(unittest.TestCase):
    def _snapshot(self, td, *, written_ms, candles_n):
        payload = {
            "schema_version": ms.MARKET_SCHEMA_VERSION,
            "written_at_ms": written_ms,
            "venue": "binance",
            "tickers": {"BTC-USDT-SWAP": {"last": 100.0, "bid": 99.9, "ask": 100.1,
                                          "open24h": 90.0, "vol24h": 5.0,
                                          "exchange_ms": 1}},
            "klines": {"BTC-USDT-SWAP": {"1H": [
                [str(1000 + i), "1", "2", "0.5", "1.5", "10", "15", "15", "1"]
                for i in range(candles_n)]}},
            "health": {},
        }
        path = os.path.join(td, "ws.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    def test_fresh_snapshot_serves_ticker_without_rest(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=int(mds.time.time() * 1000), candles_n=3)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange",
                              side_effect=AssertionError("不得回退 REST")):
                row = mds.fetch_ticker("BTC-USDT-SWAP", venue="binance")
            self.assertEqual(row["last"], "100.0")
            self.assertEqual(row["bidPx"], "99.9")

    def test_stale_snapshot_falls_back_to_rest(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=1, candles_n=3)   # 极旧
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange") as fake:
                fake.return_value.ticker.return_value = {"last": "123"}
                row = mds.fetch_ticker("BTC-USDT-SWAP", venue="binance")
            self.assertEqual(row["last"], "123", "过期快照必须回退 REST")

    def test_insufficient_candles_fall_back_to_rest(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=int(mds.time.time() * 1000), candles_n=2)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange") as fake:
                fake.return_value.candles.return_value = [
                    ["1", "1", "2", "0.5", "1.5", "10", "15", "15", "1"]]
                rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=10, venue="binance")
            self.assertEqual(len(rows), 1, "快照不足 limit 根必须回退 REST")

    def test_rest_failure_falls_back_to_the_partial_ws_series(self):
        """REST 被封/限流时，WS 里够**判据下限**就别让整包变成"数据质量 invalid"。"""
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=int(mds.time.time() * 1000), candles_n=12)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange") as fake, \
                 patch.object(mds, "note_failure") as failure:
                fake.return_value.candles.side_effect = RuntimeError("IP banned (-1003)")
                rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=24, venue="binance")
            self.assertEqual(len(rows), 12, "应改用 WS 快照里够判据的 12 根")
            self.assertTrue(failure.called, "REST 取数失败必须进失败账（否则只剩猜）")

    def test_rest_failure_without_enough_ws_rows_still_returns_empty(self):
        """WS 也不够 1H 的 8 根下限 ⇒ 仍返回空（不许拿 3 根冒充完整历史）。"""
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=int(mds.time.time() * 1000), candles_n=3)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange") as fake, \
                 patch.object(mds, "note_failure"):
                fake.return_value.candles.side_effect = RuntimeError("banned")
                rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=24, venue="binance")
            self.assertEqual(rows, [])

    def test_sufficient_candles_served_from_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td, written_ms=int(mds.time.time() * 1000), candles_n=6)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange",
                              side_effect=AssertionError("不得回退 REST")):
                rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=4, venue="binance")
            self.assertEqual(len(rows), 4)


class SeedHistoryTests(unittest.TestCase):
    """开流前的 REST 播种（2026-10-30）：WS 从连上那刻才攒 K 线，1H/4H 热身期太长。

    不播种的后果是决策链在热身期只能回退 REST，而币安 REST 一旦被封/限流，
    整包就成「数据质量 invalid」（用户报的正是这个形态）。
    """

    @staticmethod
    def _rows(n, step_ms=900_000):
        return [[str(1_000_000 + i * step_ms), "1", "2", "0.5", "1.5", "10",
                 "15", "15", "1"] for i in range(n)]

    def test_seed_history_fills_all_three_intervals(self):
        state = ms.MarketState(max_bars=240)
        seen = []

        def fetch(inst, bar, limit):
            seen.append((inst, bar, limit))
            return self._rows(limit)

        report = ms.seed_history(state, symbols=["BTC-USDT-SWAP"], venue="binance",
                                 limit=120, fetch=fetch)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["symbols"], 1)
        self.assertEqual(report["rows"], 360, "三档 × 120 根")
        self.assertEqual([b for _i, b, _l in seen], ["15m", "1H", "4H"])
        snap = state.snapshot()
        self.assertEqual(len(snap["klines"]["BTC-USDT-SWAP"]["15m"]), 120)
        self.assertEqual(len(snap["klines"]["BTC-USDT-SWAP"]["4H"]), 120)

    def test_seeded_snapshot_is_consumable_without_rest(self):
        """播种写进快照后，`fetch_candles(venue=binance)` 严格档应直接命中快照。"""
        state = ms.MarketState(max_bars=240)
        ms.seed_history(state, symbols=["BTC-USDT-SWAP"], venue="binance", limit=60,
                        fetch=lambda i, b, l: self._rows(l))
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ws.json")
            self.assertTrue(ms.write_snapshot(path, state.snapshot(), compact=True))
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange",
                              side_effect=AssertionError("播种够数就不该回退 REST")):
                rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=24,
                                         venue="binance")
        self.assertEqual(len(rows), 24)

    def test_one_failing_interval_does_not_lose_the_others(self):
        state = ms.MarketState(max_bars=240)

        def fetch(inst, bar, limit):
            if bar == "4H":
                raise RuntimeError("IP banned (-1003)")
            return self._rows(limit)

        report = ms.seed_history(state, symbols=["BTC-USDT-SWAP"], venue="binance",
                                 limit=40, fetch=fetch)
        self.assertEqual(report["symbols"], 1, "仍算成功标的（另两档已播）")
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("4H", report["errors"][0])
        snap = state.snapshot()
        self.assertEqual(len(snap["klines"]["BTC-USDT-SWAP"]["15m"]), 40)
        self.assertEqual(snap["klines"]["BTC-USDT-SWAP"].get("4H"), None)

    def test_seed_is_a_noop_for_non_binance(self):
        fetcher = ms._seed_fetch("okx")
        self.assertEqual(fetcher("BTC-USDT-SWAP", "15m", 10), [])

    def test_run_stream_seeds_before_connecting(self):
        frames = [json.dumps({"result": None, "id": 1})]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ws.json")
            snap = ms.run_stream(
                venue="binance", symbols=["BTC-USDT-SWAP"], snapshot_path=path,
                connect_factory=lambda url, **kw: _FakeWS(frames),
                max_rounds=1, reconnect_backoff=0.01, flush_seconds=0.0,
                now=lambda: 1_700_000_000_000,
                seed=True, seed_limit=30,
                seed_fetch=lambda i, b, l: self._rows(l))
            self.assertEqual(len(snap["klines"]["BTC-USDT-SWAP"]["15m"]), 30,
                             "播种结果必须在首帧之前就在快照里")
            on_disk = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertIn("BTC-USDT-SWAP", on_disk["klines"])


class DeepStreamTests(unittest.TestCase):
    """深度 / 标记价(资金费率) / 清算 三类流的解析与状态。"""

    def test_stream_params_cover_depth_markprice_and_liquidation(self):
        params = ms.binance_stream_params(["btcusdt"])
        self.assertIn("btcusdt@depth20@100ms", params)
        self.assertIn("btcusdt@markPrice@1s", params)
        self.assertEqual(params.count("!forceOrder@arr"), 1, "全局清算流只能订阅一次")

    def test_depth_frame_parsed(self):
        out = ms.parse_frame("binance", json.dumps({
            "e": "depthUpdate", "E": 123, "s": "BTCUSDT",
            "b": [["100.0", "2"], ["99.5", "3"]], "a": [["100.5", "1"]]}))
        row = out["ticks"][0]
        self.assertEqual(row["kind"], "depth")
        self.assertEqual(row["symbol"], "BTC-USDT-SWAP")
        self.assertAlmostEqual(row["bids"][0][0], 100.0)
        self.assertAlmostEqual(row["asks"][0][1], 1.0)

    def test_mark_price_frame_parsed_with_decimal_funding(self):
        out = ms.parse_frame("binance", json.dumps({
            "e": "markPriceUpdate", "E": 1, "s": "BTCUSDT",
            "p": "80000.5", "r": "0.00010000", "T": 999}))
        row = out["ticks"][0]
        self.assertEqual(row["kind"], "mark")
        self.assertAlmostEqual(row["mark_px"], 80000.5)
        self.assertAlmostEqual(row["funding_rate"], 0.0001)

    def test_liquidation_frame_and_array_frame(self):
        one = {"e": "forceOrder", "E": 5, "o": {"s": "BTCUSDT", "S": "SELL",
                                                "p": "79000", "q": "0.5"}}
        single = ms.parse_frame("binance", json.dumps(one))["ticks"][0]
        self.assertEqual(single["kind"], "liquidation")
        self.assertEqual(single["side"], "sell")
        arr = ms.parse_frame("binance", json.dumps([one, one]))["ticks"]
        self.assertEqual(len(arr), 2, "数组帧必须逐元素展开")

    def test_state_keeps_latest_depth_mark_and_recent_liquidations(self):
        state = ms.MarketState(max_liquidations=2)
        state.update({"kind": "depth", "symbol": "BTC-USDT-SWAP",
                      "bids": [[1.0, 2.0]], "asks": [[1.1, 3.0]]})
        state.update({"kind": "mark", "symbol": "BTC-USDT-SWAP",
                      "mark_px": 100.0, "funding_rate": 0.0002})
        for i in range(3):
            state.update({"kind": "liquidation", "symbol": "BTC-USDT-SWAP",
                          "side": "buy", "price": 100.0 + i, "qty": 1.0})
        self.assertEqual(len(state.depth("BTC-USDT-SWAP")["bids"]), 1)
        self.assertAlmostEqual(state.mark("BTC-USDT-SWAP")["funding_rate"], 0.0002)
        self.assertEqual(len(state.liquidations()), 2, "清算缓冲必须有界")
        snap = state.snapshot(now_ms=1)
        self.assertIn("depth", snap)
        self.assertIn("marks", snap)
        self.assertIn("liquidations", snap)


class MarketDataServiceDeepTests(unittest.TestCase):
    def _snapshot(self, td):
        payload = {
            "schema_version": ms.MARKET_SCHEMA_VERSION,
            "written_at_ms": int(mds.time.time() * 1000),
            "venue": "binance",
            "tickers": {}, "klines": {}, "liquidations": [],
            "depth": {"BTC-USDT-SWAP": {"bids": [[100.0, 2.0]], "asks": [[100.5, 1.0]]}},
            "marks": {"BTC-USDT-SWAP": {"mark_px": 100.0, "funding_rate": 0.0001}},
            "health": {},
        }
        path = os.path.join(td, "ws.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    def test_funding_rate_served_from_snapshot_as_percent(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange",
                              side_effect=AssertionError("不得回退 REST")):
                rate = mds.fetch_funding_rate("BTC-USDT-SWAP", venue="binance")
            self.assertAlmostEqual(rate, 0.01, places=6, msg="0.0001 小数 → 0.01%")

    def test_depth_served_from_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange",
                              side_effect=AssertionError("不得回退 REST")):
                book = mds.fetch_orderbook_depth("BTC-USDT-SWAP", sz=5, venue="binance")
            self.assertIsNotNone(book)
            self.assertTrue(book.get("bids"))

    def test_missing_mark_falls_back_to_rest(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._snapshot(td)
            with patch.object(mds, "_WS_SNAPSHOT_FILE", path), \
                 patch.object(mds, "_binance_exchange") as fake:
                fake.return_value.funding_rate.return_value = 0.05
                rate = mds.fetch_funding_rate("ETH-USDT-SWAP", venue="binance")
            self.assertAlmostEqual(rate, 0.05, "快照无该标的时必须回退 REST")


if __name__ == "__main__":
    unittest.main()
