"""Read-only OKX history collection with durable pagination and exact order joins."""
from __future__ import annotations
import json
import subprocess
import time
from astra_backend.analysis_store import Archive, digest, lifecycle_id, normalize_position, now_ms, timestamp_ms, decimal, number
from astra_backend.analysis_capture import emit, fault, recover_legacy
from scripts.okx_runtime import selected_environment

SOURCES = {
    "positions-history":("/api/v5/account/positions-history",["account","positions-history"],"uTime"),
    "orders":("/api/v5/trade/orders-history-archive",["swap","orders","--archive"],"ordId"),
    "fills":("/api/v5/trade/fills-history",["swap","fills","--archive"],"billId"),
    "bills":("/api/v5/account/bills-archive",["account","bills","--archive"],"billId"),
}

def fetch_page(source: str, after: str = "", env=None) -> list[dict]:
    env = env or selected_environment()
    path, cli, cursor = SOURCES[source]
    params = {"instType":"SWAP","limit":"100"}
    if after: params["after"] = after
    if env.configured:
        from astra_backend.okx_trade_service import _request
        return _request("GET",path,params,env=env,timeout=8)
    if after:
        raise RuntimeError("当前 OKX CLI 命令不传递历史分页游标；需要静态 V5 凭证进行完整回补")
    cmd = ["okx",f"--{env.mode}",*cli,"--limit","100","--json"]
    if after: cmd.extend(["--after",after])
    result = subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",timeout=8)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"OKX CLI returned {result.returncode}")
    data = json.loads(result.stdout)
    if isinstance(data,dict):
        if str(data.get("code","0")) != "0": raise RuntimeError(str(data.get("msg","OKX request failed")))
        data = data.get("data")
    if not isinstance(data,list): raise ValueError("OKX history response must be a list")
    return data

def row_ms(r: dict) -> int:
    return timestamp_ms(r.get("uTime") or r.get("ts") or r.get("fillTime") or r.get("cTime")) or 0

def sync_source(archive: Archive, account: str, source: str, fetch, max_pages: int = 3) -> dict:
    state = archive.sync_state(account,source)
    cursor_key = SOURCES[source][2]
    state.setdefault("pending",[])
    pages = 0
    def save(rows):
        archive.raw(account,source,rows)
        state["last_success_ms"] = now_ms()
        state["rows_received"] = state.get("rows_received",0)+len(rows)
        stamps = [row_ms(r) for r in rows]
        if stamps:
            state["earliest_ms"] = min(state.get("earliest_ms") or min(stamps),min(stamps))
    try:
        # Always observe the newest page, while retaining all interrupted older scans.
        head = fetch(source,"")
        pages += 1
        save(head)
        prior = state.get("watermark_ms",0)
        if len(head)>=100 and min(map(row_ms,head))>=prior:
            after = str(head[-1].get(cursor_key) or "")
            if not after: raise ValueError("历史分页缺少游标")
            pending = {"after":after,"stop_ms":prior}
            if pending not in state["pending"]: state["pending"].append(pending)
        elif prior == 0:
            state["history_exhausted"] = True
        if head: state["watermark_ms"] = max(prior,max(map(row_ms,head)))
        archive.sync_state(account,source,state)
        while state["pending"] and pages<max_pages:
            scan = state["pending"][0]
            rows = fetch(source,scan["after"])
            pages += 1
            save(rows)
            if len(rows)<100 or (rows and min(map(row_ms,rows))<scan["stop_ms"]):
                state["pending"].pop(0)
                if scan["stop_ms"]==0: state["history_exhausted"]=True
            else:
                after = str(rows[-1].get(cursor_key) or "")
                if not after or after==scan["after"]: raise ValueError("历史分页游标没有推进；保留断点等待修复")
                scan["after"] = after
            archive.sync_state(account,source,state)
        state["error"] = None
        state["complete"] = bool(state.get("history_exhausted")) and not state["pending"]
        state["coverage_note"] = "交易所当前可恢复范围；CLI 能力和交易所保留期可能限制覆盖"
    except Exception as exc:
        state["error"] = str(exc)
        state["failed_ms"] = now_ms()
        state["complete"] = False
    archive.sync_state(account,source,state)
    return state

def _closing_order(orders: list[dict], direction: str) -> dict | None:
    """The order that reduced the position: opposite side, reduce-only or market."""
    want = "sell" if direction=="long" else "buy"
    for order in orders:
        if str(order.get("side") or "").lower() != want: continue
        if str(order.get("reduceOnly") or "").lower()=="true" or str(order.get("ordType") or "").lower()=="market":
            return order
    return None

def _attached_triggers(orders: list[dict], direction: str) -> tuple:
    """Cloud OCO trigger prices (sl, tp) carried on the entry order."""
    want = "buy" if direction=="long" else "sell"
    for order in orders:
        if str(order.get("side") or "").lower() != want: continue
        for algo in order.get("attachAlgoOrds") or []:
            sl, tp = number(algo.get("slTriggerPx")), number(algo.get("tpTriggerPx"))
            if sl or tp: return sl, tp
    return None, None

def infer_exit_reason(orders: list[dict], fills_by_order: dict, direction: str) -> dict | None:
    """Classify a close from exchange facts when no captured exit event exists.

    positions-history reports every normal close as type=2 with an empty triggerPx,
    so the reason can only be inferred: an attached algo id proves the cloud OCO
    fired, and the entry order's trigger prices say which leg it was. A plain
    market reduce-only close is the engine's own judgement exit. Inferred reasons
    are never promoted to confirmed evidence.
    """
    order = _closing_order(orders, direction)
    if not order: return None
    triggered = bool(order.get("algoId")) or str(order.get("clOrdId") or "").startswith("O")
    if not triggered:
        return {"reason":"系统主动平仓","source":"system_market_close"}
    px = None
    for fill in fills_by_order.get((order.get("instId"), order.get("ordId"))) or []:
        px = number(fill.get("fillPx")) or px
    px = px or number(order.get("avgPx")) or number(order.get("px"))
    sl, tp = _attached_triggers(orders, direction)
    reason = "云端保护单触发"
    if px and sl and tp:
        hit_sl = px <= sl if direction=="long" else px >= sl
        hit_tp = px >= tp if direction=="long" else px <= tp
        if hit_sl != hit_tp:
            reason = "云端止损触发" if hit_sl else "云端止盈触发"
        else:
            reason = "云端止损触发" if abs(px-sl) <= abs(px-tp) else "云端止盈触发"
    return {"reason":reason,"source":"attached_algo_trigger"}

def reconcile(archive: Archive, account: str, positions: list[dict] | None = None) -> list[dict]:
    """Keep source financial facts separate from captured strategy evidence."""
    histories = archive.raw_rows(account,"positions-history")
    orders = archive.raw_rows(account,"orders")
    fills = archive.raw_rows(account,"fills")
    events = archive.reconciliation_events(account)
    submissions = {e["order_id"]:e for e in events if e["kind"]=="order.submitted" and e["order_id"]}
    fill_event_ids = ["fill_"+digest([account, f.get("instId"), f.get("billId") or f.get("tradeId")]) for f in fills]
    order_event_ids = ["order_state_"+digest([account, str(o.get("ordId") or ""), o.get("state"), o.get("uTime")]) for o in orders]
    event_ids = archive.existing_event_ids(account, [*fill_event_ids, *order_event_ids])
    from collections import defaultdict
    # Index once instead of rescanning the entire event history twice per trade.
    # Preserve chronological order so the last exit remains the latest evidence.
    exits_by_trade, samples_by_trade = defaultdict(list), defaultdict(list)
    for event in events:
        if event["kind"] == "position.exit_reason":
            exits_by_trade[event["trade_id"]].append(event)
        elif event["kind"] in ("position.manage.end", "position.sample"):
            samples_by_trade[event["trade_id"]].append(event)
    fills_by_order, orders_by_inst, histories_by_side = defaultdict(list), defaultdict(list), defaultdict(list)
    for fill in fills: fills_by_order[(fill.get("instId"), fill.get("ordId"))].append(fill)
    for order in orders: orders_by_inst[order.get("instId")].append(order)
    for history in histories: histories_by_side[(history.get("instId"), str(history.get("direction")))].append(history)
    live_ids = {lifecycle_id(p) for p in positions or []}
    trades = []
    for fill, event_id in zip(fills, fill_event_ids):
        oid = str(fill.get("ordId") or "")
        submitted = submissions.get(oid)
        if event_id in event_ids: continue
        archive.event(account, "order.fill", {"fill": fill}, "filled",
            id=event_id,
            occurred_ms=row_ms(fill), order_id=oid, inst=fill.get("instId"),
            decision_id=submitted["decision_id"] if submitted else "",
            cycle_id=submitted["cycle_id"] if submitted else "",
            config_id=submitted["config_id"] if submitted else "")
    for order, event_id in zip(orders, order_event_ids):
        oid = str(order.get("ordId") or "")
        submitted = submissions.get(oid)
        if event_id in event_ids: continue
        archive.event(account, "order.state", {"order": order}, str(order.get("state") or "unknown"),
            id=event_id,
            occurred_ms=row_ms(order), order_id=oid, inst=order.get("instId"),
            decision_id=submitted["decision_id"] if submitted else "",
            cycle_id=submitted["cycle_id"] if submitted else "",
            config_id=submitted["config_id"] if submitted else "")
    for row in histories:
        t = normalize_position(row)
        # The live endpoint is stronger evidence of a still-open lifecycle.
        if t["id"] in live_ids: t["status"]="partial"; t["close_ms"]=None; t["close_time"]=""
        inst = row.get("instId")
        opened = t["open_ms"] or 0
        end = timestamp_ms(row.get("uTime")) or now_ms()
        direction = t["side"]
        linked_orders = []
        for order in orders_by_inst[inst]:
            order_side = str(order.get("posSide") or "")
            if order_side not in (direction,"net"): continue
            order_fills = fills_by_order[(inst, order.get("ordId"))]
            explicit = order.get("posId") and str(order["posId"])==str(row.get("posId"))
            in_lifecycle = False
            for fill in order_fills:
                at = row_ms(fill)
                candidates = [h for h in histories_by_side[(inst, direction)] if (timestamp_ms(h.get("cTime")) or 0)<=at<=(timestamp_ms(h.get("uTime")) or now_ms())]
                if opened<=at<=end and len(candidates)==1:
                    in_lifecycle = True
            # Without fill evidence do not turn a nearby order into an exact match.
            if explicit and opened<=row_ms(order)<=end or in_lifecycle:
                linked_orders.append(order)
        ids = [str(o["ordId"]) for o in linked_orders if o.get("ordId")]
        opening = [submissions[o] for o in ids if o in submissions]
        opening.sort(key=lambda e:(e["occurred_ms"],e["id"]))
        t["order_ids"] = ids
        t["fill_ids"] = [str(f.get("billId") or f.get("tradeId")) for f in fills if str(f.get("ordId")) in ids]
        if opening:
            first = opening[0]
            t.update({"decision_id":first["decision_id"],"cycle_id":first["cycle_id"],"config_id":first["config_id"],
                      "decision_ids":list(dict.fromkeys(e["decision_id"] for e in opening if e["decision_id"])),
                      "config_ids":list(dict.fromkeys(e["config_id"] for e in opening if e["config_id"])),
                       "strategy":"AstraQuant","link_evidence":"exchange_fill_or_position_id"})
            with archive.connect() as con:
                payload = archive.read_blob(con,first["body_hash"])
                t["confidence"] = payload.get("confidence")
                t["policy_version"] = payload.get("policy_version")
                t["requested_entry_px"] = payload.get("effective",{}).get("price")
            entry = number(t.get("open_px")); requested = number(t.get("requested_entry_px"))
            if entry is not None and requested:
                t["entry_deviation_bps"] = (entry/requested-1)*10000*(1 if direction=="long" else -1)
        else:
            t["link_evidence"]="unlinked"
        exact_exits = exits_by_trade[t["id"]]
        if exact_exits:
            with archive.connect() as con:
                detail = archive.read_blob(con,exact_exits[-1]["body_hash"])
            t["exit_reason"] = detail.get("reason")
            t["exit_evidence"] = "confirmed" if detail.get("confirmed") else "inferred"
        else:
            inferred = infer_exit_reason(linked_orders, fills_by_order, direction)
            if inferred:
                t["exit_reason"] = inferred["reason"]
                t["exit_evidence"] = "inferred"
                t["exit_source"] = inferred["source"]
        samples = samples_by_trade[t["id"]]
        highs, lows = [], []
        with archive.connect() as con:
            for event in archive.iter_event_bodies(samples, con):
                body = event["body"]
                trackers = (body.get("after") or {}).get("trackers") or {}
                tracker = body.get("tracker") or trackers.get(f"{inst}_{direction}") or {}
                hi, lo = number(tracker.get("highWaterMark")), number(tracker.get("lowWaterMark"))
                if hi is not None: highs.append(hi)
                if lo is not None: lows.append(lo)
        entry = number(t.get("open_px"))
        if highs and lows and entry and entry>0:
            t["sampled_mfe_pct"] = max(0,(max(highs)-entry)/entry*100) if direction=="long" else max(0,(entry-min(lows))/entry*100)
            t["sampled_mae_pct"] = max(0,(entry-min(lows))/entry*100) if direction=="long" else max(0,(max(highs)-entry)/entry*100)
            t["extrema_basis"] = "inspection_samples_not_tick_extrema"
        trades.append(t)
    for row in positions or []:
        if decimal(row.get("pos")) not in (None,0):
            t = normalize_position(row,live=True)
            trades = [x for x in trades if x["id"]!=t["id"]]
            trades.append(t)
    archive.upsert_trades(account,trades)
    return archive.trades(account)

def sync_archive(positions: list[dict] | None = None, max_pages: int = 2, account: str | None = None) -> list[dict]:
    from astra_backend.analysis_capture import enabled
    if not enabled(): return []
    env = selected_environment()
    from astra_backend.analysis_capture import resolve_identity
    account = account or resolve_identity(env)
    archive = Archive()
    started = time.perf_counter()
    timings = {}
    recover_legacy()
    timings["legacy_seconds"] = time.perf_counter() - started
    for source in SOURCES:
        stage = time.perf_counter()
        state = sync_source(archive,account,source,lambda s,c:fetch_page(s,c,env),max_pages)
        timings[source + "_seconds"] = time.perf_counter() - stage
        if not env.configured:
            state.update({"complete":False,"pagination_supported":False,
                          "coverage_note":"CLI 只采集可访问的最近窗口，完整历史回补需要静态 V5 凭证"})
            archive.sync_state(account,source,state)
    stage = time.perf_counter()
    result = reconcile(archive,account,positions)
    timings["reconcile_seconds"] = time.perf_counter() - stage
    timings["total_seconds"] = time.perf_counter() - started
    emit("archive.sync",{"trades":len(result), "timings":timings},"completed",account=account)
    print("[analysis_archive] " + json.dumps({"trades":len(result), "timings":timings}))
    return result
