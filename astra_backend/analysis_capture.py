"""Best-effort capture at the actual decision/execution boundaries.

All capture failures are observable and must never prevent protective orders.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import threading
import sqlite3
import time
import uuid
from astra_backend.analysis_store import Archive, ROOT, now_ms, digest, sanitize, timestamp_ms, lifecycle_id

log = logging.getLogger("astra.analysis")
_CONTEXT: ContextVar[dict] = ContextVar("astra_analysis",default={})
_fault_lock = threading.Lock()
_MEMORY_FAULT: dict = {}

def enabled() -> bool:
    return os.getenv("ASTRA_ANALYSIS_DISABLED") != "1" and (os.getenv("ASTRA_TESTING") != "1" or bool(os.getenv("ASTRA_ANALYSIS_DB")))

def profile_identity() -> str:
    for module in ("okx_runtime","scripts.okx_runtime"):
        mod = sys.modules.get(module)
        frozen = getattr(mod,"_FROZEN_ENVIRONMENT",None)
        if frozen is not None: return frozen.identity
    try:
        from scripts.okx_runtime import selected_environment
        return selected_environment().identity
    except Exception:
        return "unknown:unresolved"

def identity() -> str:
    if _CONTEXT.get().get("account"):
        return _CONTEXT.get()["account"]
    profile = profile_identity()
    if profile.endswith("-oauth"):
        try:
            return Archive().sync_state(profile, "identity").get("account") or profile
        except Exception:
            pass
    return profile

def resolve_identity(env=None) -> str:
    """Bind each cycle to the frozen static credential profile."""
    from scripts.okx_runtime import current_environment
    env = env or current_environment()
    return env.identity


def venue_identity(venue: str, environment: str | None = None) -> str:
    """Keep external-venue events separate from the OKX archive."""
    from scripts.okx_runtime import current_environment
    venue = str(venue or "okx").lower()
    if venue == "okx":
        return identity()
    mode = environment or current_environment().mode
    try:
        from astra_backend.exchanges.identity import credential_fingerprint
        from astra_backend.exchanges.registry import venue_credentials
        api_key, _ = venue_credentials(venue, mode)
        return f"{venue}:{mode}:{credential_fingerprint(api_key)}"
    except Exception as exc:
        fault(exc, "account_identity")
        return f"unknown:{venue}:{mode}:" + uuid.uuid4().hex

def context() -> dict:
    return dict(_CONTEXT.get())

def fault(exc: Exception, action: str) -> None:
    global _MEMORY_FAULT
    payload = {"at_ms":now_ms(),"action":action,"error":sanitize(str(exc)),"pid":os.getpid()}
    _MEMORY_FAULT = payload
    log.error("Analysis capture failed [%s]: %s",action,payload["error"])
    try:
        path = Archive().path.parent / "analysis_capture_fault.json"
        with _fault_lock:
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
            os.replace(tmp,path)
    except Exception:
        pass

def fault_status() -> dict:
    path = Archive().path.parent / "analysis_capture_fault.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else _MEMORY_FAULT
    except Exception:
        return _MEMORY_FAULT

def clear_fault(action: str | None = None) -> None:
    """Remove a persisted fault after the same capture boundary succeeds.

    A previous transient failure must not keep the dashboard red forever.
    """
    global _MEMORY_FAULT
    path = Archive().path.parent / "analysis_capture_fault.json"
    try:
        with _fault_lock:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if action is None or payload.get("action") == action:
                    path.unlink()
            if action is None or _MEMORY_FAULT.get("action") == action:
                _MEMORY_FAULT = {}
    except Exception:
        pass

def emit(kind: str, body=None, status: str = "observed", **meta) -> str:
    if not enabled(): return ""
    try:
        ctx = context()
        ctx.update({k:v for k,v in meta.items() if v is not None})
        if isinstance(body, dict):
            body = {**body, "capture_span_id": ctx.get("span_id"), "request_id": ctx.get("request_id")}
        account = ctx.pop("account",None) or identity()
        last_exc = None
        for attempt in range(3):
            try:
                return Archive().event(account,kind,body if body is not None else {},status,**ctx)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if last_exc:
            raise last_exc
        return ""
    except Exception as exc:
        fault(exc,kind)
        return ""

def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError,ValueError) as exc:
        return {"unavailable":type(exc).__name__}

def saved_configuration() -> dict:
    """Read persisted files, never invoke loaders that create defaults or mutate state."""
    from scripts.risk_constants import DEFAULTS
    saved_env = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        # Keep the analysis collector dependency-free. The application already
        # has its own dotenv parser; this read-only subset handles KEY=value,
        # optional quotes and comments without importing python-dotenv.
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
                value = value[1:-1]
            saved_env[key] = value
    risk = {k:{"configured":saved_env.get(k),"default":v} for k,v in DEFAULTS.items()}
    names = {
        "prompt_library":"prompt_library.json","council":"council_config.json",
        "models":"llm_models.json","interceptors":"interceptor_plugins.json",
        "instrument_pool":"instrument_pool.json","memory":"structured_trading_memory.json",
        "baseline":"account_initial_state.json","schedule":"schedule_config.json",
    }
    content = {key:read_json(ROOT/"data"/filename) for key,filename in names.items()}
    content["risk"] = risk
    content["system_prompt_override"] = (ROOT/"data"/"system_prompt_override.txt").read_text(encoding="utf-8") if (ROOT/"data"/"system_prompt_override.txt").exists() else None
    plugin_dir = ROOT/"plugins"/"interceptors"
    content["interceptor_code"] = {str(p.relative_to(ROOT)):p.read_text(encoding="utf-8") for p in sorted(plugin_dir.glob("*.py"))}
    paths = ["scripts/ai_factor_trader.py","scripts/order_risk.py","scripts/risk_constants.py",
             "astra_backend/interceptor_manager.py","astra_backend/llm_manager.py","scripts/ai_brain_trader.py"]
    content["code_on_disk"] = {p:digest((ROOT/p).read_text(encoding="utf-8")) for p in paths if (ROOT/p).exists()}
    content["mode"] = saved_env.get("ASTRA_OKX_ENV")
    return sanitize(content)

def configuration(process: str, loaded: dict | None = None, effective: dict | None = None) -> str:
    if not enabled(): return ""
    try:
        from scripts.risk_constants import DEFAULTS
        body = saved_configuration()
        body["loaded_risk"] = {}
        if loaded is not None:
            for k in DEFAULTS:
                symbol = {"ASTRA_MAX_CONCURRENT_POSITIONS":"MAX_CONCURRENT_POSITIONS", "ASTRA_MAX_SINGLE_ASSET_MARGIN_USDT":"MAX_SINGLE_ASSET_MARGIN", "ASTRA_RISK_PER_TRADE_RATIO":"RISK_PER_TRADE_EQUITY_RATIO", "ASTRA_MIN_RISK_REWARD":"MIN_RISK_REWARD_RATIO"}.get(k,k.removeprefix("ASTRA_"))
                body["loaded_risk"][k] = loaded.get(symbol,{"unobserved":True})
            for key in ("ASSET_CLASS_PROFILES","TARGET_INSTRUMENTS","MAX_CONCURRENT_POSITIONS"):
                if key in loaded: body[key] = loaded[key]
        body["effective"] = effective or {}
        body["source_note"] = "进程在事件边界实际观测的值；未观测字段不推定为已加载"
        result = Archive().configuration(identity(),body,"runtime",process,os.getpid())
        clear_fault("configuration")
        return result
    except Exception as exc:
        fault(exc,"configuration")
        return ""

def decision_id(inst: str) -> str:
    ctx = context()
    cycle = ctx.get("cycle_id")
    return "d_"+digest([cycle,inst])[:28] if cycle and inst else ""

@contextmanager
def scope(**values):
    token = _CONTEXT.set({**context(),**values})
    try:
        yield
    finally:
        _CONTEXT.reset(token)

def bind_decision(inst: str, decision: dict | None = None):
    """Set context for one synchronous instrument iteration; callers reset at cycle exit."""
    d = decision or {}
    meta = d.get("analysis") or {}
    _CONTEXT.set({**context(),"inst":inst,"decision_id":meta.get("decision_id") or decision_id(inst),
                  "decision_cycle_id":meta.get("cycle_id",""),"decision_config_id":meta.get("config_id",""),
                  "trade_id":"","order_id":"","side":"", "proposal":d.get("decision") or {}, "policy_version":d.get("policy_version")})

def decision_meta(inst: str) -> dict:
    ctx = context()
    return {"decision_id":decision_id(inst),"cycle_id":ctx.get("cycle_id",""),"config_id":ctx.get("config_id","")}

def cycle(process: str):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not enabled(): return fn(*args,**kwargs)
            parent = context()
            account = resolve_identity()
            with scope(account=account):
                cfg = configuration(process,fn.__globals__)
            with scope(account=account,cycle_id=uuid.uuid4().hex,config_id=cfg,process=process,
                       decision_id="",order_id="",trade_id="",inst="",side=""):
                emit("cycle.start",{"process":process,"pid":os.getpid(),"parent_cycle_id":parent.get("cycle_id")})
                try:
                    result = fn(*args,**kwargs)
                    emit("cycle.end",{"process":process},"completed" if result is not None else "no_result")
                    return result
                except Exception as exc:
                    emit("cycle.end",{"process":process,"error":str(exc)},"failed")
                    raise
        return wrapped
    return decorate

def observed(kind: str):
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not enabled(): return fn(*args,**kwargs)
            try:
                bound = dict(signature.bind(*args,**kwargs).arguments)
            except Exception:
                return fn(*args,**kwargs)
            f = bound.get("f") or {}
            inst = bound.get("inst_id") or f.get("instId") or context().get("inst","")
            side = bound.get("pos_side") or context().get("side","")
            call_id = uuid.uuid4().hex
            pos = bound.get("curr_pos") or {}
            trade = lifecycle_id(pos) if pos.get("cTime") and pos.get("instId") else context().get("trade_id","")
            with scope(inst=inst,side=side,trade_id=trade,span_id=call_id,request_id=""):
                emit(kind+".start",{"call_id":call_id,"arguments":bound})
                try:
                    result = fn(*args,**kwargs)
                    emit(kind+".end",{"call_id":call_id,"result":result,"after":bound},"completed")
                    return result
                except Exception as exc:
                    emit(kind+".end",{"call_id":call_id,"error":str(exc)},"failed")
                    raise
        return wrapped
    return decorate

def threaded_submit(pool, fn, *args, **kwargs):
    """ThreadPoolExecutor does not propagate ContextVars on its own."""
    if not enabled():
        return pool.submit(fn,*args,**kwargs)
    return pool.submit(copy_context().run,fn,*args,**kwargs)

def llm_request(request, timeout: float, transport, **meta):
    """Observe the exact request after provider adaptation, including fallback attempts."""
    call_id = uuid.uuid4().hex
    _CONTEXT.set({**context(),"request_id":call_id})
    try:
        body = json.loads(request.data.decode("utf-8")) if request.data else None
    except Exception:
        body = request.data.decode("utf-8",errors="replace") if request.data else None
    emit("llm.request",{"call_id":call_id,"request":body,"timeout":timeout,**meta})
    try:
        response = transport(request,timeout=timeout)
    except Exception as exc:
        emit("llm.transport",{"call_id":call_id,"error":str(exc),"http_status":getattr(exc,"code",None)},"failed")
        raise
    return response

def position_meta(position: dict | None = None) -> dict:
    """Bind one event to the position lifecycle it belongs to.

    Judgement-based exits are executed per position inside a single span, so the
    ambient context cannot say which lifecycle the exit belonged to. Without an
    explicit binding the review archive has to mark the trade unlinked.
    """
    pos = position or {}
    meta = {}
    venue = str(pos.get("venue") or pos.get("exchange") or "okx").lower()
    if venue != "okx":
        meta["account"] = venue_identity(venue, pos.get("environment"))
    if pos.get("instId"): meta["inst"] = str(pos.get("instId"))
    side = str(pos.get("posSide") or pos.get("side") or "")
    if side and side != "net": meta["side"] = side
    if pos.get("cTime") and pos.get("instId"): meta["trade_id"] = lifecycle_id(pos)
    return meta


def observe_position(row: dict, tracker: dict | None = None):
    tracker = tracker or {}
    emit("position.sample",{"position":row,"tracker":tracker,
         "sampling_note":"巡检采样，非逐笔行情极值"},
         trade_id=lifecycle_id(row),inst=row.get("instId"),side=row.get("posSide") or row.get("side"))

def recover_legacy():
    """Preserve unattributed old files without claiming the current account owns them."""
    if not enabled(): return
    archive = Archive()
    account = "legacy:unknown"
    imported = archive.sync_state(account,"legacy_import")
    if imported.get("complete") and imported.get("version") == 2: return
    try:
        rows = read_json(ROOT/"data"/"trading_ledger.json")
        existing = {}
        if archive.path.exists():
            import sqlite3
            con = sqlite3.connect(archive.path.resolve().as_uri()+"?mode=ro",uri=True)
            con.row_factory = sqlite3.Row
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_trades'").fetchone():
                    existing = {r[0] for r in con.execute("SELECT id FROM analysis_trades")}
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='trades'").fetchone():
                    history = []
                    for rec in con.execute("SELECT * FROM trades"):
                        rec = dict(rec)
                        if str(rec.get("bill_id")) in existing: continue
                        history.append({"id":rec.get("bill_id"),"inst":rec.get("inst"),"side":rec.get("direction"),
                            "status":"closed" if rec.get("action") in ("closed","平仓") else "unknown",
                            "close_time":rec.get("time"),"close_px":rec.get("price"),"sz":rec.get("size"),
                            "fee":rec.get("fee"),"gross_pnl":rec.get("gross_pnl"),"pnl":rec.get("pnl"),
                            "exit_reason":rec.get("comment"),"source":"legacy_sqlite"})
                    rows = (rows if isinstance(rows,list) else []) + history
            finally:
                con.close()
        trades = []
        for row in rows if isinstance(rows,list) else []:
            if row.get("account") and str(row.get("id")) in existing: continue
            t = dict(row)
            t["id"] = "legacy_"+str(row.get("id") or digest(row))
            t["open_ms"] = timestamp_ms(row.get("open_time"))
            t["close_ms"] = timestamp_ms(row.get("close_time"))
            t["side"] = {"多":"long","空":"short"}.get(row.get("side"),row.get("side","unknown"))
            t["known_net_pnl"] = row.get("net_pnl",row.get("pnl"))
            t["net_pnl"] = None
            t["cost_complete"] = False
            t["cost_basis"] = "legacy_unverified"
            t["exit_evidence"] = "inferred" if row.get("exit_reason") else "unknown"
            t["decision_id"] = ""; t["config_id"] = ""
            trades.append(t)
        archive.upsert_trades(account,trades)
        for name in ("signal_journal.json","ai_brain_history.json"):
            rows = read_json(ROOT/"data"/name)
            for row in rows if isinstance(rows,list) else []:
                archive.event(account,"legacy."+name,row,"unlinked",id=digest([name,row]),
                              occurred_ms=timestamp_ms(row.get("entryTime") or row.get("time")) or now_ms())
        # A missing file can appear later; do not permanently skip recovery in that case.
        if trades:
            archive.sync_state(account,"legacy_import",{"complete":True,"version":2,"at_ms":now_ms(),"count":len(trades)})
    except Exception as exc:
        fault(exc,"legacy_import")
