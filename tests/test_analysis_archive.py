"""Analysis acceptance tests use temporary archives and fake exchange responses only."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from astra_backend.analysis_store import Archive, lifecycle_id, normalize_position, timestamp_ms, sanitize
from astra_backend.analysis_metrics import summarize, breakdown
from astra_backend.analysis_sync import sync_source, reconcile
from astra_backend import analysis_capture as capture, analysis_service as service
from astra_backend.analysis_routes import install_routes

START = 1788825600000
ACCOUNT = "okx:live:test-account"

def position(i=1, **overrides):
    return {"posId":str(i),"instId":"BTC-USDT-SWAP","direction":"long",
            "cTime":str(START+i*60000),"uTime":str(START+i*60000+30000),
            "type":"2","openAvgPx":"100","closeAvgPx":"102","closeTotalPos":"1","lever":"3",
            "pnl":"2","fee":"-0.1","fundingFee":"-0.2","liqPenalty":"0","settledPnl":"0",
            "realizedPnl":"1.7",**overrides}

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root/"astra_quant.db"
        self.env = patch.dict(os.environ,{"ASTRA_TESTING":"1","ASTRA_ANALYSIS_DB":str(self.path)})
        self.env.start()
        self.archive = Archive(self.path)
        self.saved = patch.object(service,"saved_configuration",return_value={"risk":{"min_rr":2},"prompts":"当前配置"})
        self.saved.start()

    def tearDown(self):
        self.saved.stop(); self.env.stop(); self.temp.cleanup()

    def test_read_only_queries_do_not_create_archive(self):
        self.assertEqual(self.archive.trades(ACCOUNT),[])
        self.assertEqual(self.archive.events(ACCOUNT),[])
        self.assertFalse(self.path.exists())

    def test_same_minute_sides_and_reopened_positions_have_distinct_ids(self):
        p=position()
        opposite=position(direction="short")
        reopened=position(cTime=str(START+90000))
        self.assertEqual(len({lifecycle_id(p),lifecycle_id(opposite),lifecycle_id(reopened)}),3)
        self.archive.upsert_trades(ACCOUNT,[normalize_position(p),normalize_position(opposite),normalize_position(reopened)])
        self.assertEqual(len(self.archive.trades(ACCOUNT)),3)

    def test_costs_precision_breakeven_partial_and_missing_costs(self):
        rows=[normalize_position(position(i,realizedPnl=v)) for i,v in enumerate(["0.000001","-1","0","2"],1)]
        rows += [normalize_position(position(5,type="1",realizedPnl="99"))]
        missing=position(6); missing.pop("realizedPnl"); missing.pop("fundingFee")
        rows.append(normalize_position(missing))
        stat=summarize(rows)
        self.assertEqual((stat["wins"],stat["losses"],stat["breakeven"]),(2,1,1))
        self.assertEqual(stat["sample_count"],4)
        self.assertEqual(stat["closed_count"],5)
        self.assertEqual(stat["incomplete_count"],1)
        self.assertEqual(stat["win_rate"],50)
        self.assertAlmostEqual(stat["net_pnl"],1.000001)
        self.assertAlmostEqual(stat["profit_factor"],2.000001)
        self.assertEqual(stat["max_realized_drawdown"],1)
        self.assertIsNone(summarize([])["win_rate"])
        self.assertIsNone(summarize(rows[:1])["profit_factor"])

    def test_cross_swap_blank_settlement_fields_stay_statistically_visible(self):
        blank=position(7,settledPnl="",nonSettleAvgPx="",realizedPnl="1.7")
        t=normalize_position(blank)
        self.assertTrue(t["cost_complete"])
        self.assertEqual(t["net_pnl"],"1.7")
        self.assertEqual(t["other_settlement"],"0")
        stat=summarize([t])
        self.assertEqual(stat["sample_count"],1)
        self.assertEqual(stat["win_rate"],100)
        self.assertAlmostEqual(stat["net_pnl"],1.7)

    def test_absent_component_is_zero_only_when_realized_identity_proves_it(self):
        proven=position(8); proven.pop("settledPnl")
        self.assertTrue(normalize_position(proven)["cost_complete"])
        unproven=position(9,realizedPnl="9"); unproven.pop("settledPnl")
        self.assertFalse(normalize_position(unproven)["cost_complete"])

    def test_exchange_realized_not_double_deducted(self):
        t=normalize_position(position())
        self.assertEqual(t["net_pnl"],"1.7")
        p=position(); p.pop("realizedPnl")
        self.assertEqual(normalize_position(p)["net_pnl"],"1.7")
        rebate=position(realizedPnl="2.2",fee="0.2",fundingFee="0")
        self.assertEqual(normalize_position(rebate)["net_pnl"],"2.2")

    def test_upsert_is_idempotent_and_separates_account_environments(self):
        t=normalize_position(position())
        for _ in range(2): self.archive.upsert_trades(ACCOUNT,[t])
        self.archive.upsert_trades("okx:demo:test-account",[t])
        self.assertEqual(len(self.archive.trades(ACCOUNT)),1)
        self.assertEqual(len(self.archive.trades("okx:demo:test-account")),1)
        self.archive.upsert_trades(ACCOUNT,[{**t,"status":"partial"}])
        self.assertEqual(self.archive.trades(ACCOUNT)[0]["status"],"closed")

    def test_durable_pagination_resumes_failure_beyond_one_hundred(self):
        rows=[position(i) for i in range(260,0,-1)]
        fail=[True]
        def fetch(source,cursor):
            selected=[r for r in rows if not cursor or int(r["uTime"])<int(cursor)]
            if cursor and fail[0]:
                fail[0]=False
                raise OSError("transient read failure")
            return selected[:100]
        s=sync_source(self.archive,ACCOUNT,"positions-history",fetch)
        self.assertFalse(s["complete"])
        self.assertTrue(s["pending"])
        restarted=Archive(self.path)
        s=sync_source(restarted,ACCOUNT,"positions-history",fetch,max_pages=4)
        self.assertTrue(s["complete"])
        self.assertEqual(len(restarted.raw_rows(ACCOUNT,"positions-history")),260)
        sync_source(restarted,ACCOUNT,"positions-history",fetch,max_pages=4)
        self.assertEqual(len(restarted.raw_rows(ACCOUNT,"positions-history")),260)

    def test_old_partial_revision_cannot_overwrite_new_closed_position(self):
        newest = position(type="2",uTime=str(START+180000))
        older = position(type="1",uTime=str(START+90000),realizedPnl="0.1")
        self.archive.raw(ACCOUNT,"positions-history",[newest,older])
        out = reconcile(self.archive,ACCOUNT,[])
        self.assertEqual(out[0]["status"],"closed")
        self.assertEqual(out[0]["net_pnl"],"1.7")

    def test_new_head_while_backfilling_does_not_lose_history(self):
        rows=[position(i) for i in range(220,0,-1)]
        def fetch(_,cursor): return [r for r in rows if not cursor or int(r["uTime"])<int(cursor)][:100]
        sync_source(self.archive,ACCOUNT,"positions-history",fetch,max_pages=1)
        rows[:0]=[position(i) for i in range(370,220,-1)]
        for _ in range(5):
            sync_source(self.archive,ACCOUNT,"positions-history",fetch,max_pages=3)
        self.assertEqual(len(self.archive.raw_rows(ACCOUNT,"positions-history")),370)
        self.assertTrue(self.archive.sync_state(ACCOUNT,"positions-history")["complete"])

    def test_reconcile_keeps_retired_instruments_and_partial_is_not_closed(self):
        self.archive.raw(ACCOUNT,"positions-history",[position(1,instId="RETIRED-USDT-SWAP"),position(2,type="1")])
        out=reconcile(self.archive,ACCOUNT,[])
        self.assertEqual({t["inst"] for t in out},{"RETIRED","BTC"})
        self.assertEqual(summarize(out)["sample_count"],1)

    def seed_linked_trade(self):
        row=position()
        tid=lifecycle_id(row)
        cfg=self.archive.configuration(ACCOUNT,{"prompt":"旧提示词","risk":{"min_rr":2}},"runtime","brain",123)
        self.archive.event(ACCOUNT,"llm.request",{"request":{"messages":[{"role":"user","content":"历史实际行情"}]}},"observed",
             id="request",occurred_ms=START-1000,cycle_id="brain-cycle",config_id=cfg)
        self.archive.event(ACCOUNT,"decision.proposed",{"proposal":{"action":"BUY_LONG"}},"proposed",
             id="decision",occurred_ms=START,cycle_id="brain-cycle",decision_id="d1",config_id=cfg,inst="BTC-USDT-SWAP")
        self.archive.event(ACCOUNT,"order.submitted",{"effective":{"price":"100"}},"accepted",
             id="order",occurred_ms=START+1000,cycle_id="execute-cycle",decision_id="d1",order_id="o1",config_id=cfg,inst="BTC-USDT-SWAP")
        self.archive.raw(ACCOUNT,"positions-history",[row])
        self.archive.raw(ACCOUNT,"orders",[{"ordId":"o1","instId":"BTC-USDT-SWAP","posSide":"long","state":"filled","uTime":row["cTime"]}])
        self.archive.raw(ACCOUNT,"fills",[{"billId":"f1","ordId":"o1","instId":"BTC-USDT-SWAP","ts":row["cTime"],"fillPx":"100"}])
        reconcile(self.archive,ACCOUNT,[])
        return cfg,tid

    def test_exact_fill_association_and_out_of_window_request_export(self):
        cfg,tid=self.seed_linked_trade()
        t=self.archive.trades(ACCOUNT)[0]
        self.assertEqual(t["decision_id"],"d1")
        self.assertEqual(t["id"],tid)
        query={"start_ms":START+60000,"end_ms":START+120000,"status":"closed"}
        bundle=service.export_bundle(ACCOUNT,query,self.archive)
        with zipfile.ZipFile(io.BytesIO(bundle)) as z:
            manifest=json.loads(z.read("manifest.json"))
            for filename,info in manifest["files"].items():
                self.assertEqual(hashlib.sha256(z.read(filename)).hexdigest(),info["sha256"])
                self.assertEqual(len(z.read(filename)),info["bytes"])
            events=[json.loads(line) for line in z.read("events.jsonl").decode("utf-8").splitlines()]
            self.assertIn("request",{e["id"] for e in events})
            config=json.loads(z.read("configurations.json"))
            self.assertEqual(config[0]["body"]["prompt"],"旧提示词")
            self.assertEqual(json.loads(z.read("summary.json"))["statistics"]["net_pnl"],1.7)
            self.assertEqual(manifest["counts"]["trades"],1)
            self.assertIn("历史实际行情",z.read("events.jsonl").decode("utf-8"))

    def test_export_reads_event_index_once_and_decodes_only_latest_runtime(self):
        old_cfg=self.archive.configuration(ACCOUNT,{"prompt":"old"},"runtime","brain",1)
        new_cfg=self.archive.configuration(ACCOUNT,{"prompt":"new"},"runtime","brain",2)
        for i in range(12):
            for kind in ("account.snapshot","execution.cycle_state","cycle.start"):
                self.archive.event(ACCOUNT,kind,{"index":i},id=f"{kind}-{i}",
                    occurred_ms=START+i,config_id=old_cfg if i<11 else new_cfg)
        self.archive.event("other-account","account.snapshot",{"index":"foreign"},
            id="foreign-snapshot",occurred_ms=START+100)
        # Snapshots outside the requested window still describe the latest state.
        query={"start_ms":START+1000,"end_ms":START+2000,"status":"closed"}
        with patch.object(self.archive,"events",wraps=self.archive.events) as events, \
             patch.object(self.archive,"read_blob",wraps=self.archive.read_blob) as read:
            bundle=service.export_bundle(ACCOUNT,query,self.archive)
        self.assertEqual(events.call_count,1)
        self.assertEqual(read.call_count,3)  # Two latest bodies and their single configuration.
        with zipfile.ZipFile(io.BytesIO(bundle)) as z:
            latest=json.loads(z.read("latest_runtime_state.json"))["observations"]
            self.assertEqual(set(latest),{"account.snapshot","execution.cycle_state"})
            self.assertTrue(all(e["body"]=={"index":11} for e in latest.values()))
            configs=json.loads(z.read("configurations.json"))
            self.assertEqual([c["id"] for c in configs],[new_cfg])
            runtime=json.loads(z.read("summary.json"))["runtime_observations"]
            self.assertEqual(runtime["brain"]["config_id"],new_cfg)
            self.assertEqual(runtime["brain"]["pid"],2)
            self.assertEqual(z.read("events.jsonl"),b"")
            info=json.loads(z.read("manifest.json"))["files"]["events.jsonl"]
            self.assertEqual(info,{"sha256":hashlib.sha256(b"").hexdigest(),"bytes":0})

    def test_export_resolves_redaction_once_and_refreshes_between_exports(self):
        secret="export-test-private-value"
        for i in range(4):
            self.archive.event(ACCOUNT,"llm.request",{"prompt":f"中文内容 {secret}"},
                occurred_ms=START+i,id=f"redact-{i}")
        query={"start_ms":START,"end_ms":START+100,"status":"closed"}
        for secrets in ({secret},set()):
            with patch.object(service,"sensitive_values",return_value=secrets) as resolve:
                bundle=service.export_bundle(ACCOUNT,query,self.archive)
            resolve.assert_called_once_with()
            with zipfile.ZipFile(io.BytesIO(bundle)) as z:
                payload=z.read("events.jsonl")
                self.assertEqual(secret in payload.decode("utf-8"),not bool(secrets))
                self.assertEqual(len(payload.decode("utf-8").splitlines()),4)
                self.assertTrue(payload.endswith(b"\n"))
                manifest=json.loads(z.read("manifest.json"))
                for name,info in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),info["sha256"])
                    self.assertEqual(len(z.read(name)),info["bytes"])

    def test_export_empty_archive_is_valid_and_does_not_create_database(self):
        query={"start_ms":START,"end_ms":START+100,"status":"closed"}
        bundle=service.export_bundle(ACCOUNT,query,self.archive)
        self.assertFalse(self.path.exists())
        with zipfile.ZipFile(io.BytesIO(bundle)) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(json.loads(z.read("trades.json")),[])
            self.assertEqual(json.loads(z.read("manifest.json"))["counts"],
                {"trades":0,"events":0,"configurations":0})
            self.assertEqual(z.read("events.jsonl"),b"")

    def test_exchange_fact_selection_preserves_nested_windows_gaps_and_boundaries(self):
        trades=[
            {"id":"outer","inst_id":"BTC-USDT-SWAP","open_ms":START+10,"close_ms":START+100,"order_ids":["exact"]},
            {"id":"nested","inst_id":"BTC-USDT-SWAP","open_ms":START+20,"close_ms":START+30},
            {"id":"later","inst_id":"BTC-USDT-SWAP","open_ms":START+200,"close_ms":START+300},
            {"id":"holding","inst_id":"ETH-USDT-SWAP","open_ms":START+400,"close_ms":None},
            {"id":"missing-open","inst_id":"SOL-USDT-SWAP","open_ms":None,"close_ms":START+50},
        ]
        historical_position=position(99)
        trades.append({"id":lifecycle_id(historical_position),"inst_id":"BTC-USDT-SWAP",
                       "open_ms":START+700,"close_ms":START+800})
        query={"start_ms":START+1000,"end_ms":START+2000,"inst":"BTC"}
        matches=service._exchange_fact_selector(trades,[{"order_id":"event-order"}],query,START+500)
        cases=[
            ("BTC",9,False),("BTC",10,True),("BTC",30,True),("BTC",90,True),
            ("BTC",100,True),("BTC",101,False),("BTC",199,False),("BTC",200,True),
            ("BTC",300,True),("BTC",301,False),("BTC",1000,True),("BTC",1999,True),
            ("BTC",2000,False),("ETH",399,False),("ETH",400,True),("ETH",500,True),
            ("ETH",501,False),("ETH",1000,False),("SOL",0,True),("SOL",51,False),
        ]
        for inst,offset,expected in cases:
            with self.subTest(inst=inst,offset=offset):
                row={"instId":inst+"-USDT-SWAP","ts":str(START+offset)}
                self.assertEqual(matches("fills",row),expected)
        self.assertTrue(matches("orders",{"ordId":"exact","instId":"OTHER","ts":"invalid"}))
        self.assertTrue(matches("fills",{"ordId":"event-order","instId":"OTHER"}))
        self.assertTrue(matches("positions-history",historical_position))
        self.assertFalse(matches("fills",historical_position))
        self.assertTrue(matches("bills",{"instId":"BTC-USDT-SWAP","uTime":str(START+90)}))
        self.assertFalse(matches("bills",{"instId":"BTC-USDT-SWAP","ts":"invalid"}))
        # A present ts takes precedence over uTime, matching the original export.
        self.assertFalse(matches("bills",{"instId":"BTC-USDT-SWAP","ts":str(START+101),"uTime":str(START+90)}))

    def test_nearby_order_without_fill_is_not_misrepresented_as_exact(self):
        self.archive.raw(ACCOUNT,"positions-history",[position()])
        self.archive.raw(ACCOUNT,"orders",[{"ordId":"o1","instId":"BTC-USDT-SWAP","posSide":"long","uTime":str(START+60000)}])
        self.archive.event(ACCOUNT,"order.submitted",{},"accepted",order_id="o1",decision_id="d1")
        result=reconcile(self.archive,ACCOUNT,[])
        self.assertFalse(result[0].get("decision_id"))

    def test_configuration_immutable_current_separate_and_body_deduplicated(self):
        a=self.archive.configuration(ACCOUNT,{"risk":{"rr":2}},"runtime","worker",1)
        same=self.archive.configuration(ACCOUNT,{"risk":{"rr":2}},"runtime","worker",1)
        b=self.archive.configuration(ACCOUNT,{"risk":{"rr":3}},"runtime","worker",1)
        self.assertEqual(a,same); self.assertNotEqual(a,b)
        self.assertEqual(service.configuration_detail(ACCOUNT,a,self.archive)["body"]["risk"]["rr"],2)
        self.assertEqual(service.configuration_detail(ACCOUNT,"current",self.archive)["source"],"saved")
        self.assertIsNone(service.configuration_detail("okx:demo:other",a,self.archive))

    def test_credentials_redacted_in_nested_body_and_free_text(self):
        value={"api_key":"abc-secret-123","max_tokens":4096,"prompt":"Authorization: Bearer abc-secret-123; sk-abcdefghijklm1234"}
        out=sanitize(value,secrets={"abc-secret-123"})
        text=json.dumps(out)
        self.assertNotIn("abc-secret-123",text)
        self.assertNotIn("sk-abcdefghijklm1234",text)
        self.assertEqual(out["max_tokens"],4096)

    def test_redaction_prefilters_preserve_regex_results_in_unicode_prompts(self):
        samples = [
            "行情分析：" + "价格=100，成交量:500；" * 2000,
            "Authorization: Bearer value-123; next",
            "api-key = 'value-123'\nsecret_key: value-456",
            "APİ_KEY=value-123 apıkey=value-456",
            "ſecret_key=value-123 paſſword=value-456",
            "ACCESS_TOKEN: value-123 refresh_token=value-456 session_token:value-789",
            "HTTPS://user:password@example.com/path httpſ://user:pass@example.com",
            "model=sk-1234567890abcdef short=sk-tiny",
            "Bearer abc@example.com api_key='Bearer value'",
            "password='sk-1234567890abcdef' https://user:pass@example.com",
            "不含凭证的中文、emoji 📈 和 İ ı ſ K",
        ]
        for text in samples:
            expected = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
            expected = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", expected)
            expected = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", expected)
            expected = re.sub(
                r"(?i)((?:api[_-]?key|secret[_-]?key|passphrase|authorization|password|access_token|refresh_token|session_token)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'\n}]+",
                r"\1[REDACTED]", expected)
            with self.subTest(prefix=text[:50]):
                self.assertEqual(sanitize(text,secrets=set()),expected)

    def test_old_sqlite_history_is_preserved_without_claiming_current_account(self):
        import sqlite3
        con=sqlite3.connect(self.path)
        con.execute("CREATE TABLE trades (bill_id TEXT,inst TEXT,direction TEXT,action TEXT,time TEXT,price REAL,size REAL,fee REAL,gross_pnl REAL,pnl REAL,comment TEXT)")
        con.execute("INSERT INTO trades VALUES ('old-1','RETIRED','多','closed','2026-09-01 12:00:00',1,2,-0.1,1,0.9,'旧推断原因')")
        con.commit(); con.close()
        with patch.object(capture,"ROOT",self.root):
            capture.recover_legacy()
            capture.recover_legacy()
        rows=self.archive.trades("legacy:unknown")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["inst"],"RETIRED")
        self.assertIsNone(rows[0]["net_pnl"])
        self.assertEqual(rows[0]["known_net_pnl"],0.9)
        self.assertFalse(self.archive.trades(ACCOUNT))

    def test_capture_failure_does_not_prevent_execution(self):
        @capture.observed("test.protection")
        def protected(x): return x+1
        with patch.object(Archive,"event",side_effect=OSError("disk full")),patch.object(capture,"fault") as fault:
            self.assertEqual(protected(2),3)
            self.assertTrue(fault.called)

    def test_authenticated_read_api_and_full_export_not_page_only(self):
        app=FastAPI()
        def auth(x_astra_session=None):
            if x_astra_session!="test": raise HTTPException(status_code=401)
        install_routes(app,auth)
        client=TestClient(app)
        self.archive.upsert_trades(ACCOUNT,[normalize_position(position(i)) for i in range(1,45)])
        query=f"?account={ACCOUNT}&start=2026-09-01&end=2026-10-01"
        headers={"X-Astra-Session":"test"}
        for endpoint in ("summary","trades","events","configurations/current","export"):
            self.assertEqual(client.get("/api/v1/admin/analysis/"+endpoint+query).status_code,401)
        response=client.get("/api/v1/admin/analysis/trades"+query,headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()["items"]),20)
        self.assertEqual(response.json()["total"],44)
        summary=client.get("/api/v1/admin/analysis/summary"+query,headers=headers).json()
        export=client.get("/api/v1/admin/analysis/export"+query,headers=headers)
        with zipfile.ZipFile(io.BytesIO(export.content)) as z:
            self.assertEqual(len(json.loads(z.read("trades.json"))),44)
            self.assertEqual(summary["statistics"],json.loads(z.read("summary.json"))["statistics"])
        self.assertEqual(client.get("/api/v1/admin/analysis/trades"+query+"&page=0",headers=headers).status_code,422)
        self.assertEqual(client.get("/api/v1/admin/analysis/summary?start=invalid",headers=headers).status_code,422)

    def test_trade_page_sql_pagination_and_count(self):
        self.archive.upsert_trades(ACCOUNT, [normalize_position(position(i)) for i in range(1, 46)])
        with self.archive.connect() as con:
            rows, total = self.archive.trade_page(ACCOUNT, {"status": "closed"}, 2, 20, con)
        self.assertEqual(total, 45)
        self.assertEqual(len(rows), 20)

if __name__=="__main__":
    unittest.main()
