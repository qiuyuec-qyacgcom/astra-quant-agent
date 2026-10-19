"""行情归一化层：把各所原始行情统一成 OKX 口径，供因子引擎按 venue 消费。

因子算法（`scripts/factors/okx_quant_factors.py`）是交易所无关的，只吃归一化后的
序列与 dict。本模块负责把币安（BinanceExchange 原始返回）映射到 OKX 口径：

- 盘口 `bids`/`asks`：OKX 每档是 `[px, sz, numOrders]` 三元组，币安只有 `[px, sz]`。
  下游 `compute_depth_factors` 的 `_robust` 用 `r[3]` 读 numOrders，缺失时记 0。
- ticker：币安 `_ticker_row` 已产出 OKX 风格字段，这里只做防御性兜底。

关键语义：**币安盘口无 numOrders 口径**。首版补 1（保守保留原始厚度），
`compute_depth_factors` 的稳健口径（`numOrders >= min_multi_order_levels`）会恒为
False，导致 OBI 稳健口径缺失——这比"误报方向"更安全（fail-closed）。
后续如需让币安微观因子可用，可启用 `BINANCE_MULTI_ORDER_SIZE` 阈值：只有当该档
数量达到下限时才视为"多笔档"（numOrders=3），让 `depth_reliable` 对厚盘口成立。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 币安无 numOrders，改用「档位数量 ≥ 该阈值」视为多笔档（等价于 OKX numOrders>=3 的
# 意图：过滤掉单笔可撤挂单对盘口厚度的支配）。默认 0 = 不额外过滤（保守补 1），
# 测试可覆盖该常量以验证厚盘口判定。
BINANCE_MULTI_ORDER_SIZE = 0.0


def normalize_venue(venue: Optional[str]) -> Optional[str]:
    """归一化 venue 字面量：None/auto/空 → None（走默认 OKX），否则小写。"""
    if venue is None:
        return None
    v = str(venue).strip().lower()
    if v in ("", "auto", "okx"):
        return None
    return v


def normalize_depth(depth: Optional[Dict[str, Any]], venue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """把盘口 dict 归一成 OKX 口径 `{bids:[[px,sz,numOrders],...], asks:[...]}`。

    OKX 盘口每档 `[px, sz, numOrders]`；币安只有 `[px, sz]`。币安时补一个合成
    numOrders（用档位 sz 阈值判定多笔档），否则下游稳健口径恒不可用。
    """
    v = normalize_venue(venue)
    if not isinstance(depth, dict):
        return None
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []

    def _norm(rows: Any) -> List[List[Any]]:
        out: List[List[Any]] = []
        for r in rows or []:
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                continue
            px, sz = r[0], r[1]
            if len(r) >= 3 and v != "binance":
                # 已带 numOrders（OKX 或已归一），原样保留
                out.append([px, sz, r[2]])
                continue
            # 币安：合成 numOrders。sz 达到阈值视为多笔档（numOrders=3），否则 1。
            try:
                sz_f = float(sz)
            except (TypeError, ValueError):
                sz_f = 0.0
            synthetic = 3 if (BINANCE_MULTI_ORDER_SIZE > 0 and sz_f >= BINANCE_MULTI_ORDER_SIZE) else 1
            out.append([px, sz, synthetic])
        return out

    return {"bids": _norm(bids), "asks": _norm(asks)}


def normalize_ticker(row: Optional[Dict[str, Any]], venue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """把 ticker dict 归一成 OKX 口径（last/bidPx/askPx/open24h/high24h/low24h/...）。

    币安 `_ticker_row` 已产出 OKX 风格字段，这里只做防御性兜底（字段缺失时补 0）。
    """
    if not isinstance(row, dict):
        return None
    keys = ("last", "bidPx", "askPx", "open24h", "high24h", "low24h", "vol24h", "volCcy24h", "ts")
    out: Dict[str, Any] = {}
    for k in keys:
        out[k] = row.get(k, "0")
    # instId 透传（如有）
    if row.get("instId") is not None:
        out["instId"] = row["instId"]
    return out


def normalize_oi(row: Optional[Dict[str, Any]], venue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """归一 OI 快照为 `{oi, oiCcy, oiUsd, ts}`。币安 `open_interest()` 已产出同构字段。"""
    if not isinstance(row, dict):
        return None
    return {
        "oi": row.get("oi", "0"),
        "oiCcy": row.get("oiCcy", "0"),
        "oiUsd": row.get("oiUsd", "0"),
        "ts": row.get("ts", ""),
    }
