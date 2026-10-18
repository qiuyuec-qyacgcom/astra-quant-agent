"""Replay actual capture hooks without exchange or model network calls."""
from __future__ import annotations
import concurrent.futures
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
from astra_backend import analysis_capture as capture
from astra_backend.analysis_store import Archive

class CaptureReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"analysis.db"
        self.env=patch.dict(os.environ,{"ASTRA_TESTING":"1","ASTRA_ANALYSIS_DB":str(self.path)})
        self.env.start()
        self.archive=Archive(self.path)
        self.account="okx:live:replay"
        self.scope=capture.scope(account=self.account,cycle_id="cycle-replay",config_id="test-config")
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None,None,None); self.env.stop(); self.temp.cleanup()

    def events(self,kind=None):
        with self.archive.connect() as con:
            return [{**e,"body":self.archive.read_blob(con,e["body_hash"])} for e in self.archive.events(self.account,con) if kind is None or e["kind"]==kind] if con else []

    def test_actual_fallback_request_and_response_are_recorded(self):
        from astra_backend import llm_manager
        response={"choices":[{"message":{"content":'{"action":"WAIT"}'}}],"usage":{"total_tokens":25}}
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        calls=[]
        def transport(req,timeout):
            payload=json.loads(req.data.decode("utf-8")); calls.append(payload)
            if len(calls)==1:
                raise urllib.error.HTTPError(req.full_url,400,"invalid parameter",{},io.BytesIO(b"temperature invalid parameter"))
            return Reply(json.dumps(response).encode("utf-8"))
        runtime={"model":"replay-model","base_url":"https://example.test/v1","api_key":"secret-for-test","api_format":"openai_chat","reasoning_type":"none","thinking_timeout":30}
        with patch.object(llm_manager,"get_active_llm_runtime",return_value=runtime),patch.object(llm_manager.urllib.request,"urlopen",side_effect=transport):
            content,_,usage,_=llm_manager.execute_llm_request([{"role":"user","content":"实际行情和持仓"}],reasoning_effort="none")
        requests=self.events("llm.request")
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]["body"]["request"],calls[0])
        self.assertEqual(requests[1]["body"]["request"],calls[1])
        self.assertNotIn("temperature",requests[1]["body"]["request"])
        self.assertTrue(requests[1]["body"]["request"]["stream"])
        recorded=self.events("llm.response")[0]
        self.assertEqual(recorded["body"]["response"],response)
        self.assertEqual(recorded["body"]["request_id"],requests[-1]["body"]["request_id"])
        self.assertEqual(usage["total_tokens"],25)

    def test_model_retry_and_failover_keep_request_response_correlation(self):
        from astra_backend import llm_manager
        response = {"choices": [{"message": {"content": '{"action":"WAIT"}'}}],
                    "usage": {"total_tokens": 32}}
        primary = {"model": "primary-test", "base_url": "https://example.test/v1",
                   "api_key": "private-test-key", "api_format": "openai_chat",
                   "reasoning_type": "none", "thinking_timeout": 10,
                   "request_attempts": 2, "fallback_model_ids": ["backup-test"]}
        backup = {**primary, "model": "backup-test"}
        calls = []
        def transport(req, timeout):
            payload = json.loads(req.data.decode("utf-8"))
            calls.append(payload)
            if payload["model"] == "primary-test":
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
            return io.BytesIO(json.dumps(response).encode("utf-8"))
        with patch.object(llm_manager, "get_active_llm_runtime", return_value=primary), \
                patch.object(llm_manager, "resolve_model_runtime", return_value=backup), \
                patch.object(llm_manager, "record_failover_event"), \
                patch.object(llm_manager.time, "sleep"), \
                patch.object(llm_manager.urllib.request, "urlopen", side_effect=transport):
            _, _, usage, _ = llm_manager.execute_llm_request(
                [{"role": "user", "content": "fixture"}], reasoning_effort="none")
        requests = self.events("llm.request")
        self.assertEqual([r["body"]["request"]["model"] for r in requests],
                         ["primary-test", "primary-test", "backup-test"])
        self.assertEqual([r["body"]["attempt"] for r in requests], [0, 1, 0])
        self.assertEqual([r["body"]["candidate_index"] for r in requests], [0, 0, 1])
        self.assertEqual(len({r["body"]["request_id"] for r in requests}), 3)
        self.assertEqual([r["body"]["http_status"] for r in self.events("llm.error")], [503, 503])
        recorded = self.events("llm.response")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["body"]["response"], response)
        self.assertEqual(recorded[0]["body"]["request_id"], requests[-1]["body"]["request_id"])
        self.assertEqual(usage["total_tokens"], 32)


    def test_parallel_seats_keep_cycle_but_separate_spans(self):
        @capture.observed("council.test")
        def seat(name):
            capture.emit("llm.response",{"seat":name})
            return name
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[capture.threaded_submit(pool,seat,n) for n in ("a","b")]
            self.assertEqual({f.result() for f in futures},{"a","b"})
        events=self.events("llm.response")
        self.assertEqual({e["cycle_id"] for e in events},{"cycle-replay"})
        self.assertEqual(len({e["body"]["capture_span_id"] for e in events}),2)


    def test_order_submission_preserves_exchange_acceptance_not_fill(self):
        scripts=str(Path(__file__).resolve().parents[1]/"scripts")
        if scripts not in sys.path: sys.path.insert(0,scripts)
        trader=importlib.import_module("scripts.ai_factor_trader")
        from tests.venue_gate_stub import direct_venue_gate_adapter
        with direct_venue_gate_adapter(), capture.scope(inst="BTC-USDT-SWAP",decision_id="decision-replay"):
            with patch.object(trader,"current_environment",return_value=SimpleNamespace(simulated=False, mode="live")), patch.object(trader,"fetch_ticker",return_value={"last":"100"}), patch("astra_backend.exchanges.listing.ensure_contract_listed",return_value=SimpleNamespace(ok=True)), patch.object(trader,"record_open_intent"), patch.object(trader.okx_rest,"place_order",return_value=[{"ordId":"order-replay"}]):
                result=trader.submit_protected_limit_order("BTC-USDT-SWAP","buy","long",2,100,130,90)
        self.assertEqual(result,(True,"order-replay"))
        event=self.events("order.submitted")[0]
        self.assertEqual(event["order_id"],"order-replay")
        self.assertEqual(event["decision_id"],"decision-replay")
        self.assertEqual(event["body"]["effective"]["sl"],90)
        self.assertFalse(self.events("order.fill"))

    def test_order_rejection_does_not_submit(self):
        scripts=str(Path(__file__).resolve().parents[1]/"scripts")
        if scripts not in sys.path: sys.path.insert(0,scripts)
        trader=importlib.import_module("scripts.ai_factor_trader")
        with patch.object(trader,"current_environment",return_value=SimpleNamespace(simulated=False, mode="live")), patch.object(trader,"fetch_ticker",return_value={"last":"100"}), patch("astra_backend.exchanges.listing.ensure_contract_listed",return_value=SimpleNamespace(ok=True)), patch.object(trader.okx_rest,"place_order") as submit:
            accepted,_=trader.submit_protected_limit_order("BTC-USDT-SWAP","buy","long",2,100,110,90)
        self.assertFalse(accepted); submit.assert_not_called()
        gate=self.events("execution.gate")[0]
        self.assertEqual(gate["status"],"rejected")

    def test_parallel_config_reads_do_not_rewrite_unchanged_configuration(self):
        from astra_backend import llm_manager, config
        path=Path(self.temp.name)/"models.json"
        fake=SimpleNamespace(llm_base_url="",llm_api_key="",llm_model="",llm_reasoning_effort="high")
        with patch.object(llm_manager,"LLM_CONFIG_FILE",path),patch.object(config,"settings",fake):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                rows=list(pool.map(lambda _:llm_manager.init_llm_config(),range(12)))
            stamp=path.stat().st_mtime_ns
            with patch.object(llm_manager,"_atomic_write_json",wraps=llm_manager._atomic_write_json) as writer:
                llm_manager.init_llm_config()
            writer.assert_not_called()
            self.assertEqual(path.stat().st_mtime_ns,stamp)
            self.assertEqual(len(rows),12)

    def test_saved_risk_change_does_not_change_loaded_snapshot(self):
        with patch.object(capture,"saved_configuration",side_effect=[{"risk":{"ASTRA_MAX_LEVERAGE":{"configured":"3"}}},{"risk":{"ASTRA_MAX_LEVERAGE":{"configured":"5"}}}]):
            a=capture.configuration("worker",{"MAX_LEVERAGE":3})
            b=capture.configuration("worker",{"MAX_LEVERAGE":3})
        self.assertNotEqual(a,b)
        with self.archive.connect() as con:
            rows=self.archive.configurations(self.account,con)
            bodies=[self.archive.read_blob(con,r["body_hash"]) for r in rows]
        self.assertEqual({b["loaded_risk"]["ASTRA_MAX_LEVERAGE"] for b in bodies},{3})

    def test_saved_configuration_does_not_require_python_dotenv_and_clears_stale_fault(self):
        from astra_backend import analysis_capture as module
        module.ROOT = Path(self.temp.name)
        (Path(self.temp.name) / ".env").write_text("ASTRA_OKX_ENV='live'\nASTRA_MAX_LEVERAGE=3\n", encoding="utf-8")
        fault_path = self.path.parent / "analysis_capture_fault.json"
        fault_path.write_text(json.dumps({"action":"configuration","error":"No module named 'dotenv'"}), encoding="utf-8")
        with patch.dict(sys.modules, {"dotenv": None}):
            body = module.saved_configuration()
            module.configuration("test-process", {"MAX_LEVERAGE": 3})
        self.assertEqual(body["mode"], "live")
        self.assertNotEqual(body["risk"]["ASTRA_MAX_LEVERAGE"]["configured"], None)
        self.assertFalse(fault_path.exists())

if __name__=="__main__":
    unittest.main()
