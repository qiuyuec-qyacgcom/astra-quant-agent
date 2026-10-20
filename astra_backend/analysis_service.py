"""Authenticated analysis queries and deterministic, self-contained exports."""
from __future__ import annotations
from bisect import bisect_right
from datetime import datetime, timedelta
import io
from astra_backend.analysis_store import Archive, BJ, now_ms, timestamp_ms, sanitize, sensitive_values, digest, lifecycle_id
from astra_backend.analysis_metrics import summarize, breakdown, VERSION, decimal
from astra_backend.analysis_capture import saved_configuration, fault_status, identity

def filters(start: str | None = None, end: str | None = None, inst: str = "", side: str = "", config_id: str = "", result: str = "", status: str = "closed") -> dict:
    today = datetime.now(BJ).date()
    # Calendar dates use Beijing midnight; end is exclusive.
    start_ms = timestamp_ms(start or str(today-timedelta(days=29)))
    end_ms = timestamp_ms(end or str(today+timedelta(days=1)))
    if start_ms is None or end_ms is None or not 0 <= start_ms < end_ms <= 253402300799999: raise ValueError("时间范围无效；结束时间为不包含的边界")
    if side not in ("","all","long","short","unknown"): raise ValueError("方向无效")
    if result not in ("","all","win","loss","breakeven"): raise ValueError("盈亏筛选无效")
    if status not in ("all","closed","holding","partial","unknown"): raise ValueError("状态筛选无效")
    return {"start_ms":start_ms,"end_ms":end_ms,"inst":inst,"side":"" if side=="all" else side,"config_id":config_id,"result":"" if result=="all" else result,"status":status}

def selected_events(events: list[dict], trades: list[dict], query: dict) -> list[dict]:
    ids = {t["id"] for t in trades}
    decisions = {v for t in trades for v in ([t.get("decision_id")]+t.get("decision_ids",[])) if v}
    orders = {v for t in trades for v in t.get("order_ids",[])}
    cycles = {t.get("cycle_id") for t in trades if t.get("cycle_id")}
    def in_window(e):
        if not query.get("start_ms",0)<=e["occurred_ms"]<query.get("end_ms",now_ms()+1): return False
        if query.get("inst") and e["inst"].replace("-USDT-SWAP","") not in ("",query["inst"]): return False
        if query.get("side") not in ("",None,"all") and e["side"] not in ("",query["side"]): return False
        if query.get("config_id") and e["config_id"]!=query["config_id"]: return False
        return True
    selected = set()
    # Include complete evidence for selected trades plus observations in the requested window.
    for _ in range(4):
        previous = len(selected)
        for e in events:
            if in_window(e) or e["trade_id"] in ids or e["decision_id"] in decisions or e["order_id"] in orders or e["cycle_id"] in cycles:
                selected.add(e["id"])
                if e["decision_id"]: decisions.add(e["decision_id"])
                if e["order_id"]: orders.add(e["order_id"])
                if e["cycle_id"]: cycles.add(e["cycle_id"])
        if len(selected)==previous: break
    return [e for e in events if e["id"] in selected]

def window_events(events: list[dict], query: dict) -> list[dict]:
    """Metrics count events in the requested window; export may carry older context."""
    return [e for e in events
        if query.get("start_ms",0) <= e["occurred_ms"] < query.get("end_ms",now_ms()+1)
        and (not query.get("inst") or e["inst"].replace("-USDT-SWAP","")==query["inst"])
        and (query.get("side") in ("",None,"all") or e["side"]==query["side"])
        and (not query.get("config_id") or e["config_id"]==query["config_id"])]

def execution_summary(events: list[dict], read_body) -> dict:
    from collections import Counter
    proposed = {e["decision_id"] for e in events if e["kind"]=="decision.proposed" and e["status"]=="proposed"}
    passed = {e["decision_id"] for e in events if e["kind"]=="decision.filtered" and e["status"]=="passed"}
    orders = {e["order_id"] for e in events if e["kind"]=="order.submitted" and e["order_id"]}
    fills = {e["order_id"] for e in events if e["kind"]=="order.fill" and e["order_id"]}
    rejections = Counter()
    for e in events:
        if e["status"] in ("rejected","failed") and e["kind"] in ("risk.rule","decision.filtered","execution.gate"):
            b = read_body(e["body_hash"]) or {}
            rejections[str(b.get("reason") or b.get("rule") or e["kind"])] += 1
    return {"proposed_decisions":len(proposed-{""}),"passed_decisions":len(passed-{""}),
            "submitted_orders":len(orders),"filled_orders":len(fills),
            "rejections":[{"reason":r,"count":n} for r,n in rejections.most_common()],
            "note":"执行漏斗按事件发生时间统计：建议／通过按决策计数，提交／成交按订单计数；被拒绝订单不估算盈亏"}

def load_summary(account: str, query: dict, archive: Archive | None = None) -> dict:
    archive = archive or Archive()
    with archive.connect() as con:
        trades = archive.trades(account,query,con) if con else []
        all_trades = archive.trades(account,con=con) if con else []
        events = selected_events(archive.events(account,con),trades,query) if con else []
        configs = archive.configurations(account,con) if con else []
        last = {}
        for e in events:
            if e["config_id"]: last[e["config_id"]] = max(last.get(e["config_id"],0),e["observed_ms"])
        for c in configs:
            c.pop("body_hash",None)
            c["last_observed_ms"] = last.get(c["id"],c["captured_ms"])
        health = archive.health(account,con) if con else archive.health(account)
        health["capture_fault"] = fault_status()
        return {"account":account,"accounts":archive.accounts(),"filters":query,"timezone":"Asia/Shanghai",
                "statistics":summarize(trades),"instruments":sorted({t["inst"] for t in all_trades}),
                "configurations":configs,"health":health,
                "execution":execution_summary(window_events(events,query),lambda key:archive.read_blob(con,key)) if con else {},
                "captured_at_ms":now_ms()}

def trade_list(account: str, query: dict, page: int = 1, page_size: int = 20, archive=None):
    archive = archive or Archive()
    if query.get("result"):
        all_rows = list(reversed(archive.trades(account, query)))
        items, total = all_rows[(page-1)*page_size:page*page_size], len(all_rows)
    else:
        with archive.connect() as con:
            items, total = archive.trade_page(account, query, page, page_size, con) if con else ([], 0)
        items = list(reversed(items))
    return {"items":items,"total":total,"page":page,"page_size":page_size,"statistics_version":VERSION}

def trade_detail(account: str, trade_id: str, archive=None) -> dict | None:
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT body_hash FROM analysis_trades WHERE account=? AND id=?",(account,trade_id)).fetchone()
        if not r: return None
        trade = archive.read_blob(con,r[0])
        query = {"start_ms":1,"end_ms":1}  # Only explicit relation expansion, no time heuristic.
        events = selected_events(archive.events(account,con),[trade],query)
        configs = sorted({e["config_id"] for e in events if e["config_id"]}|{trade["config_id"]} if trade.get("config_id") else {e["config_id"] for e in events if e["config_id"]})
        return {"trade":trade,"events":[{k:v for k,v in e.items() if k!="body_hash"} for e in events],"config_ids":configs}

def event_list(account: str, query: dict, page: int = 1, page_size: int = 30, archive=None):
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return {"items":[],"total":0}
        trades = archive.trades(account,query,con)
        events = list(reversed(selected_events(archive.events(account,con),trades,query)))
        return {"items":[{k:v for k,v in e.items() if k!="body_hash"} for e in events[(page-1)*page_size:page*page_size]],"total":len(events)}

def event_detail(account: str, event_id: str, archive=None):
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT * FROM analysis_events WHERE account=? AND id=?",(account,event_id)).fetchone()
        if not r: return None
        e = dict(r); e["body"] = archive.read_blob(con,e.pop("body_hash"))
        return sanitize(e)

def configuration_detail(account: str, config_id: str, archive=None):
    if config_id=="current":
        body = saved_configuration()
        return {"id":digest(body),"source":"saved","captured_ms":now_ms(),"body":body,
                "note":"导出时保存的配置，不代表交易进程已加载"}
    archive = archive or Archive()
    with archive.connect() as con:
        if not con: return None
        r = con.execute("SELECT * FROM analysis_configs WHERE account=? AND id=?",(account,config_id)).fetchone()
        if not r: return None
        out = dict(r); out["body"]=archive.read_blob(con,out.pop("body_hash"))
        return sanitize(out)

def _exchange_fact_selector(trades: list[dict], events: list[dict], query: dict, cutoff: int):
    """Index holding windows once instead of scanning all trades for every fact."""
    order_ids = {v for t in trades for v in t.get("order_ids",[])}|{e["order_id"] for e in events if e["order_id"]}
    position_ids = {t["id"] for t in trades}
    windows = {}
    for t in trades:
        windows.setdefault(t.get("inst_id"),[]).append((t.get("open_ms") or 0,t.get("close_ms") or cutoff))
    indexed = {}
    for inst, spans in windows.items():
        starts, ends = [], []
        for start, end in sorted(spans):
            starts.append(start)
            # Prefix maxima preserve nested/overlapping windows as well as gaps.
            ends.append(max(ends[-1],end) if ends else end)
        indexed[inst] = starts, ends

    def matches(source: str, row: dict) -> bool:
        if source=="positions-history" and lifecycle_id(row) in position_ids: return True
        if str(row.get("ordId") or "") in order_ids: return True
        stamp = timestamp_ms(row.get("ts") or row.get("uTime")) or 0
        if query["start_ms"] <= stamp < query["end_ms"] and (
            not query.get("inst") or str(row.get("instId","")).replace("-USDT-SWAP","")==query["inst"]
        ):
            return True
        spans = indexed.get(row.get("instId"))
        if spans is None: return False
        starts, ends = spans
        index = bisect_right(starts,stamp)-1
        return index >= 0 and stamp <= ends[index]

    return matches


def export_bundle(account: str, query: dict, archive=None) -> bytes:
    """Compatibility entry point; HTTP exports use write_bundle with a disk file."""
    from astra_backend.analysis_export import write_bundle
    out = io.BytesIO()
    write_bundle(account, query, out, archive)
    return out.getvalue()
