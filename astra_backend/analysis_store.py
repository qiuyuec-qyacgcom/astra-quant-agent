"""Durable, account-isolated analysis archive. Reads never create a database."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
import zlib
from typing import Any
from astra_backend.analysis_metrics import decimal, number
from astra_backend.time_utils import to_millis

ROOT = Path(__file__).resolve().parents[1]
BJ = timezone(timedelta(hours=8))
SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_blobs (
 hash TEXT PRIMARY KEY, content BLOB NOT NULL, raw_bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_configs (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, captured_ms INTEGER NOT NULL,
 source TEXT NOT NULL, process TEXT NOT NULL, pid INTEGER, body_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_configs_account ON analysis_configs(account, captured_ms);
CREATE TABLE IF NOT EXISTS analysis_events (
 id TEXT PRIMARY KEY, account TEXT NOT NULL, occurred_ms INTEGER NOT NULL, observed_ms INTEGER NOT NULL,
 kind TEXT NOT NULL, status TEXT NOT NULL, cycle_id TEXT NOT NULL DEFAULT '',
 decision_id TEXT NOT NULL DEFAULT '', order_id TEXT NOT NULL DEFAULT '',
 trade_id TEXT NOT NULL DEFAULT '', inst TEXT NOT NULL DEFAULT '', side TEXT NOT NULL DEFAULT '',
 config_id TEXT NOT NULL DEFAULT '', body_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_events_time ON analysis_events(account, occurred_ms);
CREATE INDEX IF NOT EXISTS analysis_events_decision ON analysis_events(account, decision_id);
CREATE INDEX IF NOT EXISTS analysis_events_order ON analysis_events(account, order_id);
CREATE INDEX IF NOT EXISTS analysis_events_trade ON analysis_events(account, trade_id);
CREATE INDEX IF NOT EXISTS analysis_events_cycle ON analysis_events(account, cycle_id);
CREATE TABLE IF NOT EXISTS analysis_trades (
 account TEXT NOT NULL, id TEXT NOT NULL, inst TEXT NOT NULL, side TEXT NOT NULL,
 status TEXT NOT NULL, open_ms INTEGER, close_ms INTEGER, config_id TEXT NOT NULL DEFAULT '',
 decision_id TEXT NOT NULL DEFAULT '', body_hash TEXT NOT NULL, updated_ms INTEGER NOT NULL,
 PRIMARY KEY(account,id)
);
CREATE INDEX IF NOT EXISTS analysis_trades_time ON analysis_trades(account, status, close_ms);
CREATE TABLE IF NOT EXISTS analysis_raw (
 account TEXT NOT NULL, source TEXT NOT NULL, id TEXT NOT NULL, occurred_ms INTEGER NOT NULL,
 body_hash TEXT NOT NULL, PRIMARY KEY(account,source,id)
);
CREATE INDEX IF NOT EXISTS analysis_raw_time ON analysis_raw(account, source, occurred_ms);
CREATE TABLE IF NOT EXISTS analysis_sync (
 account TEXT NOT NULL, source TEXT NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(account,source)
);
PRAGMA user_version=1;
"""
SECRET_KEY = re.compile(r"(^|[_-])(api[_-]?keys?|secret|passphrase|password|authorization|cookies?|credentials?|access[_-]?token|refresh[_-]?token|session([_-]?token)?|close[_-]?token|token[_-]?hash|salt)([_-]|$)", re.I)
REDACTED = "[REDACTED]"

def db_path() -> Path:
    return Path(os.environ.get("ASTRA_ANALYSIS_DB") or ROOT / "data" / "astra_quant.db")

def now_ms() -> int:
    return time.time_ns() // 1_000_000

def timestamp_ms(value: Any) -> int | None:
    if value is None or value == "": return None
    try:
        n = float(value)
        if n != n or abs(n) == float("inf"): return None
        return int(to_millis(n))
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=BJ)
            return int(dt.timestamp()*1000)
        except ValueError:
            return None

def time_text(ms: Any) -> str:
    return datetime.fromtimestamp(int(ms)/1000, BJ).strftime("%Y-%m-%d %H:%M:%S") if ms is not None else ""

def sensitive_values() -> set[str]:
    values = {v for k,v in os.environ.items() if SECRET_KEY.search(k) and len(v) >= 6}
    # Configs may contain plaintext provider credentials. Never return these values to callers.
    p = ROOT / "data" / "llm_models.json"
    if p.exists():
        try:
            def visit(v):
                if isinstance(v, dict):
                    for k,x in v.items():
                        if SECRET_KEY.search(k) and isinstance(x,str) and len(x)>=6: values.add(x)
                        else: visit(x)
                elif isinstance(v,list):
                    for x in v: visit(x)
            visit(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return values

def sanitize(value: Any, secrets: set[str] | None = None, *, _text_sanitizer=None) -> Any:
    secrets = sensitive_values() if secrets is None else secrets
    if isinstance(value, dict):
        return {str(k): REDACTED if SECRET_KEY.search(str(k)) else sanitize(v,secrets,_text_sanitizer=_text_sanitizer) for k,v in value.items()}
    if isinstance(value, (list,tuple,set)):
        return [sanitize(v,secrets,_text_sanitizer=_text_sanitizer) for v in value]
    if isinstance(value, str):
        if _text_sanitizer is not None:
            return _text_sanitizer(value)
        for secret in sorted(secrets,key=len,reverse=True): value = value.replace(secret,REDACTED)
        # Long model prompts usually contain none of these patterns. Cheap
        # necessary-condition checks avoid four full regex scans per string.
        # Python IGNORECASE also matches dotted/dotless I and long S; preserve
        # those matches in the prefilter instead of assuming ASCII text.
        folded = value.casefold().replace("\u0131","i").replace("i\u0307","i")
        if "bearer" in folded:
            value = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+",r"\1[REDACTED]",value)
        if "sk-" in value:
            value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}",REDACTED,value)
        if "://" in value and "@" in value:
            value = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", value)
        if (":" in value or "=" in value) and any(marker in folded for marker in (
            "api", "secret", "passphrase", "authorization", "password", "access_token", "refresh_token", "session_token"
        )):
            value = re.sub(r"(?i)((?:api[_-]?key|secret[_-]?key|passphrase|authorization|password|access_token|refresh_token|session_token)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'\n}]+",r"\1[REDACTED]",value)
        return value
    if isinstance(value,float) and (value != value or abs(value)==float("inf")): return None
    if isinstance(value,(int,float,bool)) or value is None: return value
    return str(value)

def canonical(value: Any) -> bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")

def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

_SCHEMA_LOCK = threading.Lock()


class Archive:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else db_path()

    @contextmanager
    def connect(self, write: bool = False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self.path),timeout=5.0)
        else:
            con = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5.0)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            if write:
                with _SCHEMA_LOCK:
                    con.execute("PRAGMA journal_mode=WAL")
                    con.executescript(SCHEMA)
            elif not con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_events'").fetchone():
                yield None
                return
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            yield con
            if write: con.commit()
        except BaseException:
            if write: con.rollback()
            raise
        finally:
            con.close()

    def blob(self, con, body: Any) -> str:
        raw = canonical(sanitize(body))
        key = hashlib.sha256(raw).hexdigest()
        con.execute("INSERT OR IGNORE INTO analysis_blobs VALUES (?,?,?)",(key,zlib.compress(raw),len(raw)))
        return key

    @staticmethod
    def read_blob(con, key: str) -> Any:
        row = con.execute("SELECT content FROM analysis_blobs WHERE hash=?",(key,)).fetchone()
        return json.loads(zlib.decompress(row[0]).decode("utf-8")) if row else None

    def configuration(self, account: str, body: dict, source: str, process: str = "", pid: int | None = None) -> str:
        clean = sanitize(body)
        key = digest({"account":account,"source":source,"process":process,"body":clean})
        with self.connect(True) as con:
            con.execute("INSERT OR IGNORE INTO analysis_configs VALUES (?,?,?,?,?,?,?)",
                (key,account,now_ms(),source,process,pid,self.blob(con,clean)))
        return key

    def event(self, account: str, kind: str, body: Any, status: str = "observed", **meta) -> str:
        event_id = str(meta.get("id") or uuid.uuid4().hex)
        observed = now_ms()
        # replace=True：同一稳定 id 的事件按「最新覆盖」语义写入，旧 blob 由
        # prune_events 统一回收。仅对按生命周期去重的事件（position.sample）使用。
        replace = bool(meta.get("replace"))
        with self.connect(True) as con:
            row = (event_id,account,int(meta.get("occurred_ms") or observed),observed,kind,status,
                 *[str(meta.get(k) or "") for k in ("cycle_id","decision_id","order_id","trade_id","inst","side","config_id")],
                 self.blob(con,body))
            if replace:
                con.execute("""INSERT INTO analysis_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET occurred_ms=excluded.occurred_ms,observed_ms=excluded.observed_ms,
                    kind=excluded.kind,status=excluded.status,cycle_id=excluded.cycle_id,decision_id=excluded.decision_id,
                    order_id=excluded.order_id,trade_id=excluded.trade_id,inst=excluded.inst,side=excluded.side,
                    config_id=excluded.config_id,body_hash=excluded.body_hash""", row)
            else:
                con.execute("INSERT OR IGNORE INTO analysis_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        return event_id

    def prune_events(self, keep_days: int | None = None, vacuum: bool = True) -> dict:
        """删除超过保留期的 position.sample 事件，并回收失去引用的孤儿 blob。

        设计约束（每条都有实测理由，勿放宽）：

        - 只动 `position.sample`：它是唯一按「持仓数 × 周期数」线性膨胀的事件；
          其它事件（llm.* / order.* / decision.*）每个决策周期有界，不构成膨胀主因；
        - 时间基准用 `occurred_ms`：position.sample 未显式传 occurred_ms，等于 observed_ms；
        - 删除事件后回收不再被任何 events/raw/trades/configs 引用的孤儿 blob
          （replace 覆盖也会产生孤儿 blob，这里一并处理）；
        - 只有真删了行才 VACUUM；VACUUM 失败不算错误，返回 vacuumed=False；
        - 保留天数默认 7 天，可用 ASTRA_ANALYSIS_KEEP_DAYS 覆盖（每次调用读取）。
        """
        days = keep_days if keep_days is not None else int(os.getenv("ASTRA_ANALYSIS_KEEP_DAYS", "7"))
        if days < 1:
            raise ValueError("keep_days 必须 >= 1：0 或负数会清空全部历史")
        cutoff = now_ms() - days * 24 * 3600 * 1000
        deleted = 0
        with self.connect(True) as con:
            cur = con.execute(
                "DELETE FROM analysis_events WHERE kind='position.sample' AND occurred_ms < ?", (cutoff,))
            deleted = cur.rowcount or 0
            # 回收孤儿 blob：任何不再被 events/raw/trades/configs 引用的正文。
            con.execute("""DELETE FROM analysis_blobs WHERE hash NOT IN (
                SELECT body_hash FROM analysis_events
                UNION SELECT body_hash FROM analysis_raw
                UNION SELECT body_hash FROM analysis_trades
                UNION SELECT body_hash FROM analysis_configs
            )""")
        vacuumed = False
        if deleted and vacuum:
            try:
                with self.connect(True) as con:
                    con.execute("VACUUM")
                vacuumed = True
            except sqlite3.Error:
                vacuumed = False
        return {"deleted": deleted, "vacuumed": vacuumed, "keep_days": days, "cutoff_ms": cutoff}

    def raw(self, account: str, source: str, rows: list[dict]) -> None:
        with self.connect(True) as con:
            for r in rows:
                raw_id = str(r.get("billId") or r.get("tradeId") or r.get("ordId") or digest(r))
                if source == "positions-history": raw_id = lifecycle_id(r)
                stamp = timestamp_ms(r.get("uTime") or r.get("ts") or r.get("cTime")) or 0
                con.execute("""INSERT INTO analysis_raw VALUES (?,?,?,?,?)
                    ON CONFLICT(account,source,id) DO UPDATE SET occurred_ms=excluded.occurred_ms,body_hash=excluded.body_hash WHERE excluded.occurred_ms >= analysis_raw.occurred_ms""",
                    (account,source,raw_id,stamp,self.blob(con,r)))

    def raw_rows(self, account: str, source: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.raw_rows(account,source,reader) if reader else []
        return [self.read_blob(con,r[0]) for r in con.execute("SELECT body_hash FROM analysis_raw WHERE account=? AND source=? ORDER BY occurred_ms",(account,source))]

    def iter_raw_rows(self, account: str, source: str, con):
        """Decode one exchange record at a time, with no per-record SQL lookup."""
        if con is None:
            return
        rows = con.execute("""SELECT b.content FROM analysis_raw r
            LEFT JOIN analysis_blobs b ON b.hash=r.body_hash
            WHERE r.account=? AND r.source=? ORDER BY r.occurred_ms""", (account,source))
        for row in rows:
            if row[0] is None:
                raise ValueError("分析归档正文缺失")
            yield json.loads(zlib.decompress(row[0]))

    def iter_event_bodies(self, events: list[dict], con):
        """Batch compressed blob reads; retain only one decoded event body."""
        for offset in range(0,len(events),128):
            batch = events[offset:offset+128]
            keys = list({e["body_hash"] for e in batch})
            placeholders = ",".join("?" for _ in keys)
            blobs = {r[0]:r[1] for r in con.execute(
                f"SELECT hash,content FROM analysis_blobs WHERE hash IN ({placeholders})", keys)}
            for event in batch:
                data = blobs.get(event["body_hash"])
                if data is None:
                    raise ValueError("分析归档正文缺失")
                yield {**{k:v for k,v in event.items() if k!="body_hash"},
                       "body":json.loads(zlib.decompress(data))}

    def sync_state(self, account: str, source: str, value: dict | None = None) -> dict:
        with self.connect(value is not None) as con:
            if con is None: return {}
            if value is not None:
                con.execute("INSERT OR REPLACE INTO analysis_sync VALUES (?,?,?)",(account,source,json.dumps(sanitize(value),ensure_ascii=False)))
                return value
            r = con.execute("SELECT body_json FROM analysis_sync WHERE account=? AND source=?",(account,source)).fetchone()
            return json.loads(r[0]) if r else {}

    def upsert_trades(self, account: str, trades: list[dict]) -> None:
        with self.connect(True) as con:
            for t in trades:
                t = dict(t)
                prev = con.execute("SELECT body_hash FROM analysis_trades WHERE account=? AND id=?",(account,t["id"])).fetchone()
                if prev:
                    old = self.read_blob(con,prev[0])
                    for k in ("decision_id","config_id","cycle_id","confidence","policy_version","exit_reason","exit_evidence"):
                        if not t.get(k) and old.get(k): t[k] = old[k]
                    if old.get("status") == "closed" and t.get("status") != "closed": continue
                t["account"] = account
                con.execute("""INSERT INTO analysis_trades VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account,id) DO UPDATE SET status=excluded.status,open_ms=excluded.open_ms,
                    close_ms=excluded.close_ms,config_id=excluded.config_id,decision_id=excluded.decision_id,
                    body_hash=excluded.body_hash,updated_ms=excluded.updated_ms""",
                    (account,t["id"],t.get("inst",""),t.get("side",""),t.get("status","unknown"),t.get("open_ms"),t.get("close_ms"),
                     t.get("config_id") or "",t.get("decision_id") or "",self.blob(con,t),now_ms()))

    def trade_page(self, account: str, filters: dict | None = None, page: int = 1, page_size: int = 20, con=None) -> tuple[list[dict], int]:
        if con is None:
            with self.connect() as reader:
                return self.trade_page(account, filters, page, page_size, reader) if reader else ([], 0)
        filters = filters or {}
        where, args = ["account=?"], [account]
        for key in ("inst", "side", "config_id", "status"):
            if filters.get(key) and filters[key] != "all": where.append(f"{key}=?"); args.append(filters[key])
        for key, op in (("start_ms", ">="), ("end_ms", "<")):
            if filters.get(key) is not None: where.append(f"COALESCE(close_ms,open_ms) {op} ?"); args.append(filters[key])
        clause = " AND ".join(where)
        rows = con.execute(f"SELECT body_hash FROM analysis_trades WHERE {clause} ORDER BY COALESCE(close_ms,open_ms),id LIMIT ? OFFSET ?", args + [page_size, (page-1)*page_size]).fetchall()
        total = con.execute(f"SELECT COUNT(*) FROM analysis_trades WHERE {clause}", args).fetchone()[0]
        result = [self.read_blob(con, r[0]) for r in rows]
        if filters.get("result") not in (None, "", "all"):
            wanted = filters["result"]
            result = [t for t in result if (lambda v: v is not None and {"win":v>0,"loss":v<0,"breakeven":v==0}.get(wanted, True))(decimal(t.get("net_pnl")))]
        return result, total

    def trades(self, account: str, filters: dict | None = None, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.trades(account,filters,reader) if reader else []
        filters = filters or {}
        sql, args = "SELECT body_hash FROM analysis_trades WHERE account=?", [account]
        for key in ("inst","side","config_id","status"):
            if filters.get(key) and filters[key] != "all":
                sql += f" AND {key}=?"; args.append(filters[key])
        for key,op in (("start_ms",">="),("end_ms","<")):
            if filters.get(key) is not None:
                sql += f" AND COALESCE(close_ms,open_ms) {op} ?"; args.append(filters[key])
        result = [self.read_blob(con,r[0]) for r in con.execute(sql+" ORDER BY COALESCE(close_ms,open_ms),id",args)]
        if filters.get("result") not in (None,"","all"):
            def matches(t):
                v = decimal(t.get("net_pnl"))
                return v is not None and {"win":v>0,"loss":v<0,"breakeven":v==0}.get(filters["result"],True)
            result = [t for t in result if matches(t)]
        return result

    def events(self, account: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.events(account,reader) if reader else []
        return [dict(r) for r in con.execute("SELECT * FROM analysis_events WHERE account=? ORDER BY occurred_ms,id",(account,))]

    def reconciliation_events(self, account: str) -> list[dict]:
        """Read evidence only; unrelated telemetry must not scale reconciliation."""
        with self.connect() as con:
            if con is None:
                return []
            return [dict(r) for r in con.execute(
                """SELECT * FROM analysis_events WHERE account=? AND kind IN
                ('order.submitted','position.exit_reason','position.manage.end','position.sample')
                ORDER BY occurred_ms,id""", (account,))]

    def existing_event_ids(self, account: str, ids) -> set[str]:
        """Bounded primary-key lookups, not a full account event materialization."""
        keys = list(dict.fromkeys(ids))
        found = set()
        with self.connect() as con:
            if con is None:
                return found
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset+400]
                marks = ','.join('?' for _ in batch)
                found.update(r[0] for r in con.execute(
                    f'SELECT id FROM analysis_events WHERE account=? AND id IN ({marks})',
                    [account, *batch]))
        return found

    def configurations(self, account: str, con=None) -> list[dict]:
        if con is None:
            with self.connect() as reader:
                return self.configurations(account,reader) if reader else []
        return [dict(r) for r in con.execute("SELECT * FROM analysis_configs WHERE account=? ORDER BY captured_ms DESC",(account,))]

    def accounts(self) -> list[str]:
        with self.connect() as con:
            if con is None: return []
            return [r[0] for r in con.execute("SELECT account FROM analysis_events UNION SELECT account FROM analysis_trades UNION SELECT account FROM analysis_configs ORDER BY account")]

    def health(self, account: str, con=None) -> dict:
        if con is None:
            with self.connect() as reader:
                if reader: return self.health(account,reader)
                return {"available":False,"bytes":0,"sync":[],"last_observed_ms":None,"coverage_start_ms":None}
        last = con.execute("SELECT MAX(observed_ms),MIN(occurred_ms) FROM analysis_events WHERE account=?",(account,)).fetchone()
        coverage = con.execute("SELECT MIN(open_ms),MAX(close_ms) FROM analysis_trades WHERE account=?",(account,)).fetchone()
        sync = [{"source":r[0],**json.loads(r[1])} for r in con.execute("SELECT source,body_json FROM analysis_sync WHERE account=?",(account,))]
        size = sum(p.stat().st_size for p in (self.path,Path(str(self.path)+"-wal")) if p.exists())
        return {"available":True,"bytes":size,"sync":sync,"last_observed_ms":last[0],
                "coverage_start_ms":coverage[0] or last[1],"coverage_end_ms":coverage[1],
                "coverage_note":"覆盖已归档及交易所可恢复历史；不代表账户全部历史"}

def lifecycle_id(row: dict) -> str:
    # posId alone can survive a reopen. The opening timestamp and direction distinguish lifecycles.
    return "pos_"+digest([row.get("instId"),row.get("posId") or "",row.get("cTime"),row.get("direction") or row.get("posSide")])[:32]

def normalize_position(row: dict, live: bool = False) -> dict:
    opened = timestamp_ms(row.get("cTime"))
    updated = timestamp_ms(row.get("uTime"))
    direction = str(row.get("direction") or row.get("posSide") or "unknown")
    if direction == "net":
        pos = decimal(row.get("pos"))
        direction = "long" if pos and pos > 0 else "short" if pos and pos < 0 else "unknown"
    def component(key):
        # OKX blanks fields that cannot apply to the instrument (settledPnl is
        # cross-FUTURES only). A present-but-blank value is a stated zero, while
        # an absent key stays unknown.
        if key not in row: return None
        value = row.get(key)
        return decimal("0") if isinstance(value,str) and not value.strip() else decimal(value)
    gross, fee = component("pnl"), component("fee")
    funding, penalty, settled = (component(k) for k in ("fundingFee","liqPenalty","settledPnl"))
    # A blank realizedPnl is unknown, not zero: the component sum then becomes
    # the net result instead of a false break-even.
    realized = decimal(row.get("realizedPnl"))
    components = [gross,fee,funding,penalty,settled]
    if realized is not None and None in components:
        # OKX defines realizedPnl = pnl + fee + fundingFee + liqPenalty + settledPnl.
        # When the captured components already reconcile to realizedPnl, the
        # uncaptured ones contribute nothing: that is proof, not the assumption
        # that unknown costs are zero.
        if sum((x for x in components if x is not None),Decimal(0)) == realized:
            components = [x if x is not None else Decimal(0) for x in components]
            gross, fee, funding, penalty, settled = components
    components_complete = all(x is not None for x in components)
    net = realized if realized is not None else (gross+fee+funding+penalty+settled if components_complete else None)
    # OKX type 1/4 are partial close/liquidation; only type 2/3/5 are final.
    kind = str(row.get("type",""))
    status = "holding" if live else "partial" if kind in {"1","4"} else "closed" if kind in {"2","3","5"} else "unknown"
    reason = "liquidation" if kind == "3" else "adl" if kind == "5" else None
    def val(v): return str(v) if v is not None else None
    def lifecycle_margin():
        """Closed lifecycles carry no margin field, so estimate it like the web ledger does."""
        exchange = row.get("margin") or row.get("imr")
        if exchange: return decimal(exchange), "exchange"
        if live: return None, "unknown"
        qty = decimal(row.get("closeTotalPos") or row.get("openMaxPos"))
        entry, lever = decimal(row.get("openAvgPx")), decimal(row.get("lever"))
        ct_val = None
        try:
            from scripts.instrument_pool import load_instruments
            for item in load_instruments():
                if str(item.get("instId")) == str(row.get("instId")):
                    ct_val = decimal(item.get("ctVal")); break
        except Exception:
            ct_val = None
        if qty and entry and lever and ct_val:
            return qty*ct_val*entry/lever, "notional_estimate"
        return None, "unknown"
    margin, margin_basis = lifecycle_margin()
    return {
        "id":lifecycle_id(row),"pos_id":row.get("posId"),"venue":str(row.get("venue") or "okx").lower(),
        "inst":str(row.get("instId","")).replace("-USDT-SWAP",""),
        "inst_id":row.get("instId"),"side":direction,"status":status,"open_ms":opened,
        "close_ms":updated if status=="closed" else None,"open_time":time_text(opened),
        "close_time":time_text(updated) if status=="closed" else "",
        "open_px":row.get("avgPx") if live else row.get("openAvgPx"),"close_px":row.get("closeAvgPx"),
        "sz":row.get("pos") if live else (row.get("closeTotalPos") or row.get("openMaxPos")),"lever":row.get("lever"),
        "margin":val(margin),"margin_basis":margin_basis,"unrealized_pnl":row.get("upl") if live else None,
        "mark_px":row.get("markPx") if live else None,
        "gross_pnl":val(gross),"fee":val(fee),"funding_fee":val(funding),
        "other_settlement":val(penalty+settled) if penalty is not None and settled is not None else None,
        "net_pnl":val(net) if not live else None,"pnl":val(net) if not live else None,
        # Exchange realizedPnl is authoritative for the net result, but it does
        # not prove that fee/funding/liquidation components were all captured.
        # Keep those two facts separate so the completeness percentage cannot
        # look perfect merely because one aggregate field exists.
        "cost_complete":components_complete and net is not None and not live,
        "cost_basis":"exchange_realizedPnl" if realized is not None else "complete_components" if components_complete else "incomplete",
        "components_complete":components_complete,
        "known_net_pnl":val(gross+fee) if gross is not None and fee is not None else None,
        "exit_reason":reason,"exit_evidence":"confirmed" if reason else "unknown",
        "strategy":"unknown","source":("binance_positions" if str(row.get("venue") or "").lower() == "binance" else "okx_positions"),"raw_type":kind,
    }
