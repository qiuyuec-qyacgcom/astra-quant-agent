"""周期快照的两段纯装配（B3 抽取第十七块）。

从 `scripts/ai_factor_trader.py::execute_portfolio` 搬出"只做数据装配"的长块。

## 1. `build_state_payload` —— 面板/巡检状态快照

原实现 25 行的手工字典拼装（含 17 个字段的逐项拷贝与四舍五入）。
搬出后字段清单集中一处，新增面板字段不必在 652 行里翻找。

## 2. `venue_position_span` —— 「持仓构成」一行（2026-09 新增纯函数）

不是搬家，是**缺陷修复**抽出的纯函数：巡检通知与 AI 提示词此前把场所写死成
`持仓 OKX {n}/{max}`，而系统实际在三个所上跑 ⇒ 通知读起来像"只有 OKX 有仓"。
现在系统是 **OKX 专用**（外所执行面整体移除），口径回归"只报 OKX" —— 但这是
**真实的全量**，不再是"漏报另两所"，故函数保留、跨所参数移除。

两处调用点（`cycle_stages.py` 的巡检通知与 `pos_desc`）都必须共用它 —— 各自
再写一份口径就是本缺陷的成因。

## 已随多所执行面拆除的段落

- `collect_pending_inst_ids`（外所挂单枚举）—— 外所挂单不存在了，枚举归零；
- `broken_execution_venues`（执行闸开着却不可就绪的外所）—— 同一原因。

## 边界说明（有意保守）

本模块**只搬装配**。同一函数里另有三处"从订单对象推基础币种"的表达式，
看似可合并，但**三者语义并不相同**，合并即行为变更 —— 故不动。

"""
from __future__ import annotations

from typing import Dict, List


def build_state_payload(*, timestamp_full, active_pos_count, max_positions, long_count,
                        short_count, cb_active, cb_reason, executed_actions,
                        all_factors, evaluate_asset_signal):
    """装配写入 `trading_state.json` 的字典（面板与巡检的读取源）。

    逐字保留原实现的字段顺序与四舍五入（`round(..., 1)` / `round(..., 2)`）——
    面板按这些键取值，缺一个或改一次精度都会显示错。

    `evaluate_asset_signal` 注入：每个标的的 `(score, action, strat_tag, strat_desc)`
    由它现算，保证快照与执行用的是**同一次**信号求值口径。
    """
    payload = {
        "timestamp": timestamp_full,
        "active_positions_count": active_pos_count,
        "max_positions": max_positions,
        "long_count": long_count,
        "short_count": short_count,
        "circuit_breaker": {"active": cb_active, "reason": cb_reason},
        "executed_actions": executed_actions,
        "instruments": []
    }

    # ★ 2026-10：缺失渲染助手（不臆造数值 / 不臆造方向）
    def _round_or_none(value, ndigits):
        return round(value, ndigits) if isinstance(value, (int, float)) else None

    def _trend_label(bullish):
        if bullish is None:
            return "--"          # 未知：既不是多头也不是空头
        return "多头" if bullish else "空头"

    for f in all_factors:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        payload["instruments"].append({
            "name": f["name"],
            "instId": f["instId"],
            "type": f["type"],
            "price": f["price"],
            # ★ 2026-10「不许假数据」：这里的 `.get(key, 兜底值)` 会把"没取到"
            #   写进 trading_state.json 供看板展示 —— 50.0/0.0/1.0/NEUTRAL/CHOP 每一条
            #   都会被读成"已观测的市场状态"。缺失一律 None（前端渲染 `--`），
            #   字符串型一律 "--"，方向未知一律 "--"（不再默认显示"空头"）。
            "rsi": _round_or_none(f.get("rsi"), 1),
            "rsi_7": _round_or_none(f.get("rsi_7"), 1),
            "vwap_bias": _round_or_none(f.get("vwap_bias"), 2),
            "macd_hist": f.get("macd_hist"),
            "macd_accel": f.get("macd_accel"),
            "obv_flow": f.get("obv_flow") or "--",
            "bb_bandwidth": f.get("bb_bandwidth"),
            "vol_ratio": f.get("vol_ratio"),
            "market_regime": f.get("market_regime") or "--",
            "structure_1h": f.get("structure_1h") or "--",
            "trend_1h": _trend_label(f.get("trend_1h_bullish")),
            "trend_4h": _trend_label(f.get("trend_4h_bullish")),
            "score": score,
            "action": action,
            "strategy": strat_tag,
            "desc": strat_desc,
            "position": f["position"]
        })

    return payload


def venue_position_span(*, okx_count, okx_long, okx_short, binance_count=0,
                        binance_long=0, binance_short=0, max_positions):
    """巡检通知与 AI 提示词共用的「持仓构成」一行（2026-09 用户报缺陷后新增）。

    OKX 专用化后系统只有一个场所，故只报 OKX —— 与旧实现"跨所拉不到"时的
    退化输出**逐字相同**（`持仓 N/max (多L/空S)｜okx N`），区别在于那曾是
    "未知/漏报"，现在是**真实全量**。

    币安接入执行面后，币安持仓 > 0 时追加 `binance N` 段；币安为 0 时输出
    与旧版逐字一致（保证 OKX 主所行为不变）。

    返回形如：

        持仓 7/9 (多4/空3)｜okx 7
        持仓 7/9 (多4/空3)｜okx 5 binance 2

    纯函数：不碰网络、不读全局，便于逐条钉住口径。
    """
    if binance_count:
        return (f"持仓 {int(okx_count + binance_count)}/{max_positions} "
                f"(多{int(okx_long + binance_long)}/空{int(okx_short + binance_short)})"
                f"｜okx {int(okx_count)} binance {int(binance_count)}")
    return (f"持仓 {int(okx_count)}/{max_positions} "
            f"(多{int(okx_long)}/空{int(okx_short)})｜okx {int(okx_count)}")


def split_positions_by_venue(positions) -> Dict[str, List[int]]:
    """活跃持仓按**场所**拆分笔数/多空数（纯函数，零 IO）。

    口径与 `fetch_positions_and_reconcile` 的计数段逐位一致：只看 `pos > 0`。
    场所取 `venue`/`exchange`，缺失按 `okx`（OKX 直签行不带 venue 字段）。

    存在理由（2026-10-30 用户报缺陷）：主脑的持仓行来自**本轮执行场所**的运行时，
    币安档下每一行都带 `venue="binance"`，而巡检通知/AI 提示词把**总数**当成
    `okx_count` 传入 `venue_position_span` ⇒ 币安仓被印成 `okx 1`，看日志的人
    以为仓在 OKX。本函数给出真实拆分，供调用点据实拼「持仓构成」。
    """
    counts: Dict[str, List[int]] = {}
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        try:
            size = float(p.get("pos", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        venue = str(p.get("venue") or p.get("exchange") or "okx").strip().lower() or "okx"
        slot = counts.setdefault(venue, [0, 0, 0])
        slot[0] += 1
        side = str(p.get("posSide", "net")).lower()
        if "long" in side:
            slot[1] += 1
        elif "short" in side:
            slot[2] += 1
    return counts


def position_span_from_rows(*, rows, max_positions, venue_position_span) -> str:
    """据**真实持仓行**拼「持仓构成」一行（巡检通知与 AI 提示词同源）。

    OKX 按本所计数；其余场所的活跃行统一计入 `binance` 段（登记执行面只有这两所，
    历史外所行随执行面一起下线，不会出现在本轮运行时读数里）。
    `binance` 为 0 时输出与旧版逐字一致（OKX 主所行为不变）。
    """
    counts = split_positions_by_venue(rows)
    okx = counts.get("okx", [0, 0, 0])
    other = [0, 0, 0]
    for venue, slot in counts.items():
        if venue == "okx":
            continue
        other[0] += slot[0]
        other[1] += slot[1]
        other[2] += slot[2]
    return venue_position_span(
        okx_count=okx[0], okx_long=okx[1], okx_short=okx[2],
        binance_count=other[0], binance_long=other[1], binance_short=other[2],
        max_positions=max_positions)
