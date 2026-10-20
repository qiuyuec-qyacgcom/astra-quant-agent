"""币安平仓周期行 → 台账交易记录（双所化：币安也写主脑台账）。

从 `astra_backend/exchanges/binance.py::_closed_cycle` 产出的平仓周期行翻译成
台账里的一笔 closed 交易，字段结构与 `okx_history.build_okx_trade` 对齐，
仅 `venue` 与「出场原因」口径不同。

## 与 OKX 的关键差异

1. **`venue="binance"`**：这是「切换交易所后自进化引擎能按所过滤到币安记录」的前提。
2. **盈亏口径**：币安 `_closed_cycle` 的 `pnl` 是 `realizedPnl`（**已含手续费**的净盈亏），
   `fee` 是手续费（负值）。而 OKX 台账里 `gross_pnl` 是不含手续费的毛盈亏、
   `net_pnl = gross + fee`。故币安这里 `gross = pnl - fee`、`net = pnl`，
   保证 `net = gross + fee` 的台账不变量在双所一致。
3. **出场原因诚实推断**：币安侧拿不到平仓单（无 OKX 的 reduceOnly 订单历史），
   无法用订单身份确认止盈/止损/分批。故按净盈亏阈值做**推定**并显式标注
   `exit_reason_source="inferred"`（机制确认 = `mechanism`），绝不伪装成机制确认。
4. **杠杆缺失**：币安 `_closed_cycle` 不返回杠杆（`lever=None`），台账 `lever` 记为 `"--"`，
   不臆造；`margin` 用 `净盈亏 / pnlRatio` 兜底失败后回退 500U（与 OKX 同纪律）。

## 设计取舍

- 签名与 `build_okx_trade` 对称，但**去掉 `close_orders`**（币安无平仓单匹配），
  避免把 OKX 的平仓单 join 逻辑错误套到币安上。
- `id_suffix` 由调用方算好传入（同 OKX：跨行自增序号属于调用方循环）。
"""


def build_binance_trade(*, h, reset_time, allowed, env, tz_bj, id_suffix,
                        datetime, get_ct_val):
    """把币安 `_closed_cycle` 行翻译成台账记录；不该收录时返回 `None`。

    `h` 关键字段（见 `_closed_cycle` 返回）：`instId`(BTC-USDT-SWAP 已归一)、
    `direction`(long/short)、`openAvgPx`、`closeAvgPx`、`pnl`(净、含费)、
    `fee`(负)、`openFee`、`closeFee`、`closeTotalPos`、`openMaxPos`、
    `cTime`(ms)、`uTime`(ms)、`posId`、`type`。
    """
    c_ts = int(h.get("cTime", 0) or 0) / 1000.0
    u_ts = int(h.get("uTime", 0) or 0) / 1000.0
    open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
    close_time = datetime.datetime.fromtimestamp(u_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if u_ts > 0 else "--"

    if close_time < reset_time:
        return None

    inst_id = h.get("instId", "")
    if inst_id not in allowed:
        return None
    inst = inst_id.replace("-USDT-SWAP", "")
    direction = str(h.get("direction", "")).lower()
    side = "多" if "long" in direction else "空"

    open_px = float(h.get("openAvgPx", 0) or 0)
    close_px = float(h.get("closeAvgPx", 0) or 0)
    # 币安 pnl 已含手续费（净），fee 为负
    net_pnl = float(h.get("pnl", 0) or 0)
    fee = float(h.get("fee", 0) or 0)          # 负值
    gross_pnl = net_pnl - fee                  # 还原毛盈亏

    lever_raw = h.get("lever")
    try:
        lever = int(float(lever_raw)) if lever_raw not in (None, "", 0) else None
    except (TypeError, ValueError):
        lever = None

    ct_val = get_ct_val(inst)
    close_pos_sz = float(h.get("closeTotalPos", 0) or h.get("openMaxPos", 0) or 0)

    if close_pos_sz > 0 and open_px > 0 and ct_val > 0:
        notional = close_pos_sz * ct_val * open_px
        margin_usdt = round(notional / lever, 2) if lever else round(notional, 2)
    else:
        pnl_ratio = float(h.get("pnlRatio", 0) or 0)
        margin_usdt = 500.0
        if pnl_ratio != 0:
            est_margin = abs(gross_pnl / pnl_ratio)
            margin_usdt = round(est_margin, 2)

    roi_pct = round((net_pnl / margin_usdt * 100) if margin_usdt > 0 else 0.0, 2)

    # Duration
    try:
        t1 = datetime.datetime.strptime(open_time, "%Y-%m-%d %H:%M:%S")
        t2 = datetime.datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S")
        dur_mins = int((t2 - t1).total_seconds() / 60)
        duration_str = f"{dur_mins}分钟" if dur_mins < 60 else f"{dur_mins//60}时{dur_mins%60}分"
    except Exception:
        duration_str = "--"

    strat_tag = "🌊 顺势做多" if side == "多" else "⚡ 阻力高空"

    # 出场原因诚实推断：无平仓单可匹配，按净盈亏阈值推定，显式标 inferred。
    if net_pnl > 3.0:
        exit_reason = "🎯 止盈推定（币安无平仓单匹配）"
    elif net_pnl < -1.0:
        exit_reason = "🛑 止损出场"
    else:
        exit_reason = "🛡️ 保本平仓"

    open_fee = float(h.get("openFee", 0) or 0)
    close_fee = float(h.get("closeFee", 0) or 0)

    _stable = str(h.get("posId") or "").strip() or (str(int(c_ts)) if c_ts > 0 else str(int(u_ts)))
    return {
        "id": f"binance_hist_{_stable}_{inst}{id_suffix}",
        "inst": inst,
        "side": side,
        "venue": "binance",
        "account_mode": env.mode.upper(),
        "environment": env.mode.lower(),
        "lever": f"{lever}x" if lever else "--",
        "strategy": strat_tag,
        "margin": margin_usdt,
        "sz": round(close_pos_sz, 4),
        "open_time": open_time,
        "open_px": round(open_px, 4),
        "close_time": close_time,
        "close_px": round(close_px, 4),
        "gross_pnl": round(gross_pnl, 2),
        "open_fee": round(open_fee, 4),
        "close_fee": round(close_fee, 4),
        "fee": round(fee, 2),
        "funding_fee": 0.0,
        "pnl": round(net_pnl, 2),
        "net_pnl": round(net_pnl, 2),
        "roi": roi_pct,
        "roi_pct": roi_pct,
        "duration": duration_str,
        "status": "closed",
        "exit_reason": exit_reason,
        # 双所化：币安出场原因是交易所侧推断，非机制确认
        "exit_reason_source": "inferred",
    }
