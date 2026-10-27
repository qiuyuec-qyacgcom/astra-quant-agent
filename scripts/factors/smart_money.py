"""聪明钱与大户多空明细数据采集模块（Pillar 5）。

为解决 OKX 内部 CLI 移除后聪明钱数据缺失（US-014 遗留）的问题，本模块提供单源采集：
1. 数据源：OKX Rubik 官方公开统计端点（合约多空账户数/持仓比 + 深度成交量）；
2. 容灾：数据源不可用时优雅返回 None，保留显式缺失语义，绝不伪造虚假中性信号。
"""
from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_smart_money_for_symbol(
    ccy: str,
    price: float = 0.0,
    *,
    timeout: float = 3.5,
) -> Optional[Dict[str, Any]]:
    """采集单标的顶级大户持仓多空比与主动资金流（OKX Rubik 公开统计端点）。"""
    base_ccy = str(ccy or "").upper().strip()
    if not base_ccy:
        return None

    res_okx = _fetch_from_okx_rubik(base_ccy, price=price, timeout=timeout)
    if res_okx and res_okx.get("longShortRatio"):
        return res_okx

    return None


def _fetch_from_okx_rubik(ccy: str, price: float = 0.0, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    w_long: Optional[float] = None
    ls_ratio: Optional[float] = None
    net_notional_usd = 0.0
    taker_str = "--"

    try:
        url = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-pos-ratio?ccy={ccy}&period=5m"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                raw_ratio = float(d["data"][0][1])
                ls_ratio = raw_ratio
                w_long = round(raw_ratio / (1.0 + raw_ratio), 4)
    except Exception:
        pass

    if w_long is None:
        try:
            url2 = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m"
            req2 = urllib.request.Request(url2, headers=_HEADERS)
            with urllib.request.urlopen(req2, timeout=timeout) as resp:
                d2 = json.loads(resp.read().decode("utf-8"))
                if d2.get("code") == "0" and d2.get("data") and len(d2["data"]) > 0:
                    raw_ratio2 = float(d2["data"][0][1])
                    ls_ratio = raw_ratio2
                    w_long = round(raw_ratio2 / (1.0 + raw_ratio2), 4)
        except Exception:
            pass

    if w_long is None:
        return None

    try:
        url_t = f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m"
        req_t = urllib.request.Request(url_t, headers=_HEADERS)
        with urllib.request.urlopen(req_t, timeout=timeout) as resp:
            d_t = json.loads(resp.read().decode("utf-8"))
            if d_t.get("code") == "0" and d_t.get("data") and len(d_t["data"]) > 0:
                b_vol = float(d_t["data"][0][1])
                s_vol = float(d_t["data"][0][2])
                net_notional_usd = b_vol - s_vol
                taker_str = (
                    f"{round(net_notional_usd / 1e4, 1)}万 U"
                    if abs(net_notional_usd) >= 1e4
                    else f"{round(net_notional_usd, 0)} U"
                )
    except Exception:
        pass

    return {
        "longShortRatio": {
            "weightedLongRatio": w_long,
            "longShortRatio": ls_ratio,
        },
        "notional": {
            "netNotionalUsdt": net_notional_usd,
        },
        "winRate": {},
        "takerNetUsd": taker_str,
        "lsRatio": ls_ratio,
        "weighted_long_pct": round(w_long * 100, 1),
    }


_BINANCE_FAPI = "https://fapi.binance.com"


def _bn_get(path: str, params: Dict[str, Any], timeout: float) -> Any:
    from urllib.parse import urlencode
    url = _BINANCE_FAPI + path + "?" + urlencode(params)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_from_binance(ccy: str, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """币安等价口径（官方 USDⓈ-M 文档 `/futures/data/*`）。

    - ``topLongShortPositionRatio`` → 精英持仓多空比（对应 OKX long-short-pos-ratio）
    - ``globalLongShortAccountRatio`` → 全网账户多空比（对应 OKX long-short-account-ratio）
    - ``takerlongshortRatio`` → 主动买卖净额（对应 OKX taker-volume）
    """
    symbol = f"{str(ccy or '').upper().strip()}USDT"
    ls_ratio: Optional[float] = None
    for path in ("/futures/data/topLongShortPositionRatio",
                 "/futures/data/globalLongShortAccountRatio"):
        try:
            rows = _bn_get(path, {"symbol": symbol, "period": "5m", "limit": 1}, timeout)
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                v = rows[0].get("longShortRatio")
                if v is not None:
                    ls_ratio = float(v)
                    break
        except Exception:
            continue
    if ls_ratio is None:
        return None
    w_long = round(ls_ratio / (1.0 + ls_ratio), 4)
    net_notional_usd = 0.0
    taker_str = "--"
    try:
        rows = _bn_get("/futures/data/takerlongshortRatio",
                       {"symbol": symbol, "period": "5m", "limit": 1}, timeout)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            b_vol = float(rows[0].get("buyVol") or 0.0)
            s_vol = float(rows[0].get("sellVol") or 0.0)
            net_notional_usd = b_vol - s_vol
            taker_str = (
                f"{round(net_notional_usd / 1e4, 1)}万 U"
                if abs(net_notional_usd) >= 1e4
                else f"{round(net_notional_usd, 0)} U"
            )
    except Exception:
        pass
    return {
        "longShortRatio": {"weightedLongRatio": w_long, "longShortRatio": ls_ratio},
        "notional": {"netNotionalUsdt": net_notional_usd},
        "winRate": {},
        "takerNetUsd": taker_str,
        "lsRatio": ls_ratio,
        "weighted_long_pct": round(w_long * 100, 1),
    }


def fetch_smart_money_pool(
    instruments: List[Dict[str, Any]],
    *,
    max_workers: int = 6,
    timeout: float = 3.5,
    venue: Optional[str] = None,
) -> Dict[str, Any]:
    """并发采集标的池内全部币种的聪明钱与大户数据，返回标准 pool 字典。

    venue="binance"：走币安官方 ``/futures/data/*`` 三个等价端点
    （精英持仓多空比 / 全网账户多空比 / 主动买卖量），取不到仍优雅返回空池。
    """
    pool: Dict[str, Any] = {}
    if not instruments:
        return pool
    if str(venue or "").strip().lower() == "binance":
        def _fetch_bn(item: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
            ccy = item.get("ccy") or item.get("name") or (
                item.get("instId", "").split("-")[0] if "-" in item.get("instId", "") else "")
            return ccy, _fetch_from_binance(ccy, timeout=timeout) if ccy else None

        with ThreadPoolExecutor(max_workers=min(max_workers, len(instruments))) as executor:
            for ccy, sm in executor.map(_fetch_bn, instruments):
                if ccy and sm:
                    pool[ccy] = sm
        return pool

    def _fetch_one(item: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
        ccy = item.get("ccy") or item.get("name") or (item.get("instId", "").split("-")[0] if "-" in item.get("instId", "") else "")
        price = float(item.get("price") or 0.0)
        res = fetch_smart_money_for_symbol(ccy, price=price, timeout=timeout)
        return ccy, res

    with ThreadPoolExecutor(max_workers=min(max_workers, len(instruments))) as executor:
        for ccy, sm in executor.map(_fetch_one, instruments):
            if ccy and sm:
                pool[ccy] = sm

    return pool
