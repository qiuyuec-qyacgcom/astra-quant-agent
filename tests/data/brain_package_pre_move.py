def fetch_single_instrument_package(item: Dict[str, Any], *,
                                   fetch_candles,
                                   fetch_single_indicator,
                                   fetch_ticker=None,
                                   fetch_funding_rate=None,
                                   fetch_open_interest=None,
                                   fetch_long_short_ratio=None,
                                   fetch_taker_volume=None,
                                   venue: str | None = None) -> Dict[str, Any]:
    """装配单标的数据包；行情函数由门面在**调用时**注入（理由见模块 docstring）。

    `fetch_ticker`/`fetch_funding_rate`/`fetch_open_interest`/`fetch_long_short_ratio`/
    `fetch_taker_volume` 默认 None（向后兼容老调用方只注入两个行情函数）：None 时
    对应字段取不到数据、fail-soft 保持缺省（price=0 / fundingRate=0 / oiUsd="N/A" /
    lsRatio="N/A" / takerNetUsd="N/A"），不抛异常。

    `venue="binance"` 时 K线/实时价/资金费率/OI/多空比/主动买卖量走币安公开行情
    （`long_short_ratio`/`taker_volume` 已由币安数据门户 `/futures/data/*` 提供等价
    口径）；币安真正缺失的是聪明钱（精英账户比/持仓比/大户均仓）与 Rubik 专有序列
    （OI 历史/清算脉冲），这些保持 fail-closed 缺失。
    """
    inst_id = item["instId"]
    name = item["name"]
    ccy = item.get("ccy", "")
    headers = {"User-Agent": "Mozilla/5.0"}
    is_binance = (str(venue or "").strip().lower()) == "binance"
    # 取数失败告警的措辞按场所说实话：OKX 有 www/aws/CLI 三级容灾，币安只有一套
    # 公开行情（WS 快照优先、REST 兜底）—— 沿用 OKX 的文案会让排障方向跑偏。
    _candles_src = "币安公开行情(WS/REST)" if is_binance else "www/aws/CLI 三级容灾"

    pkg = {
        "instId": inst_id,
        "name": name,
        "type": item["type"],
        "precision": item["precision"],
        "price": 0.0,
        "chg24h": 0.0,
        "bidPx": 0.0,
        "askPx": 0.0,
        "fundingRate": 0.0,
        "oiUsd": "N/A",
        "vol24h": 0.0,
        "lsRatio": "N/A",
        "takerNetUsd": "N/A",
        "atr": 0.0,
        "rsi": 50.0,
        "vwap_bias": 0.0,
        "macd_hist": 0.0,
        "macd_accel": 0.0,
        # ★「不许假数据」：1.0 会被读成"量能正常"这个**结论**；不写就是缺失
        "vol_ratio": None,
        "obv_flow": "NEUTRAL",
        "adx_1h": 0.0,
        "smart_money": {
            "available": False,
            "reason": "Top100 加权多空比/净流无公开 V5 等价接口；持仓方向证据改用 OKX 官方 top-trader 的精英账户比/精英持仓比（见 T0 衍生品梯队）",
            "weighted_long_pct": "--",
            "net_flow_usdt": "--",
            "avg_long_entry": "--",
            "avg_short_entry": "--",
            "top_win_rate": "--"
        },
        "recent_15m": [],
        "recent_1h": [],
        "recent_4h": [],
        "data_quality": "invalid"
    }

    # 1. Ticker（决策链 venue 接线：统一走注入的 fetch_ticker，不再硬编码 OKX URL）
    t_okx0 = time.time()
    try:
        if fetch_ticker is not None:
            t = fetch_ticker(inst_id, venue=venue)
            if isinstance(t, dict) and t.get("last") is not None:
                pkg["price"] = float(t.get("last", 0))
                # 只做「缺失 → 用现价兜底」；**不修正** ask<bid（那是脏数据信号，
                # 必须让六条判据把它判成 invalid，绝不能被兜底掩盖）。
                _bid = float(t.get("bidPx") or 0.0)
                _ask = float(t.get("askPx") or 0.0)
                pkg["bidPx"] = _bid if _bid > 0 else pkg["price"]
                pkg["askPx"] = _ask if _ask > 0 else pkg["price"]
                op = float(t.get("open24h", 0) or 0)
                pkg["chg24h"] = round(((pkg["price"] - op) / op * 100) if op > 0 else 0, 2)
                pkg["vol24h"] = round(float(t.get("vol24h", 0) or 0), 2)
                pkg["okx_latency_ms"] = max(1, int(round((time.time() - t_okx0) * 1000)))
    except Exception as exc:
        note_failure("okx_ticker", exc)

    # 2. 15M Candles (recent 24, about 6 hours) & Technical Indicators Calculation
    try:
        d = {"data": fetch_candles(inst_id, bar="15m", limit=24, venue=venue)}
        if d["data"]:
            raw_candles = d["data"]
            pkg["recent_15m"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_candles[:12]]

            # Calculate 15M indicators
            if len(raw_candles) >= 15:
                closes = [float(c[4]) for c in reversed(raw_candles)]
                highs = [float(c[2]) for c in reversed(raw_candles)]
                lows = [float(c[3]) for c in reversed(raw_candles)]
                vols = [float(c[5]) for c in reversed(raw_candles)]

                # ATR 15M
                tr_list = []
                for i in range(1, len(closes)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                    tr_list.append(tr)
                if len(tr_list) >= 14:
                    pkg["atr_15m"] = round(sum(tr_list[-14:]) / 14, 4)
                    pkg["atr"] = pkg["atr_15m"]

                # RSI 15M
                diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                gains = [d if d > 0 else 0 for d in diffs]
                losses = [-d if d < 0 else 0 for d in diffs]
                if len(gains) >= 14:
                    avg_g = sum(gains[-14:]) / 14
                    avg_l = sum(losses[-14:]) / 14
                    rs = (avg_g / avg_l) if avg_l > 0 else 100.0
                    pkg["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 1)
                    pkg["rsi_15m"] = pkg["rsi"]

                # VWAP Bias
                pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
                v_sum = sum(vols)
                if v_sum > 0:
                    vwap = pv_sum / v_sum
                    pkg["vwap_bias"] = round((pkg["price"] - vwap) / vwap * 100, 2)

                # Volume Ratio（**最后一根已收盘**的量 vs 它前面 5 根均量）
                #
                # ⚠️ 2026-10 实盘发现：原实现用 `vols[-1]` —— 那是 OKX 返回的
                # **正在跳动的未收盘 15M K 线**，刚开盘时成交量接近 0，
                # 于是提示词里长期出现 `15M量比=0.01x / 0.02x`，模型会读成
                # "成交量枯竭"。改用 `vols[-2]`（最近一根**已收盘**）与
                # `vols[-7:-2]` 对齐比较，比值才可解释。
                if len(vols) >= 7:
                    avg_v5 = sum(vols[-7:-2]) / 5
                    if avg_v5 > 0:
                        pkg["vol_ratio"] = round(vols[-2] / avg_v5, 2)

                # OBV Flow
                obv = 0
                for i in range(1, len(closes)):
                    if closes[i] > closes[i-1]:
                        obv += vols[i]
                    elif closes[i] < closes[i-1]:
                        obv -= vols[i]
                pkg["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")
        else:
            print(f"[AI Brain] ⚠️ {inst_id} 15m K线获取失败（{_candles_src} 未取回），本包 15M 微观指标降级缺省")
    except Exception as exc:
        print(f"[AI Brain] ⚠️ {inst_id} 15m K线处理异常: {exc}")

    # 3. 1H Candles (recent 24, about 24 hours) & 1H ATR / 1H RSI
    try:
        d = {"data": fetch_candles(inst_id, bar="1H", limit=24, venue=venue)}
        if d["data"]:
            raw_1h = d["data"]
            pkg["recent_1h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_1h[:12]]
            if len(raw_1h) >= 15:
                closes_1h = [float(c[4]) for c in reversed(raw_1h)]
                highs_1h = [float(c[2]) for c in reversed(raw_1h)]
                lows_1h = [float(c[3]) for c in reversed(raw_1h)]

                tr_list_1h = []
                for i in range(1, len(closes_1h)):
                    tr = max(highs_1h[i] - lows_1h[i], abs(highs_1h[i] - closes_1h[i-1]), abs(lows_1h[i] - closes_1h[i-1]))
                    tr_list_1h.append(tr)
                if len(tr_list_1h) >= 14:
                    pkg["atr_1h"] = round(sum(tr_list_1h[-14:]) / 14, 4)
                    pkg["atr"] = pkg["atr_1h"]  # Elevate primary ATR to 1H

                diffs_1h = [closes_1h[i] - closes_1h[i-1] for i in range(1, len(closes_1h))]
                gains_1h = [d if d > 0 else 0 for d in diffs_1h]
                losses_1h = [-d if d < 0 else 0 for d in diffs_1h]
                if len(gains_1h) >= 14:
                    avg_g_1h = sum(gains_1h[-14:]) / 14
                    avg_l_1h = sum(losses_1h[-14:]) / 14
                    rs_1h = (avg_g_1h / avg_l_1h) if avg_l_1h > 0 else 100.0
                    pkg["rsi_1h"] = round(100.0 - (100.0 / (1.0 + rs_1h)), 1)

                # 1H Swing Structure
                if len(closes_1h) >= 10:
                    ma7_1h = sum(closes_1h[-7:]) / 7
                    ma20_1h = sum(closes_1h[-20:]) / min(len(closes_1h), 20)
                    if closes_1h[-1] > ma7_1h > ma20_1h:
                        pkg["structure_1h"] = "1H_SWING_BULL"
                    elif closes_1h[-1] < ma7_1h < ma20_1h:
                        pkg["structure_1h"] = "1H_SWING_BEAR"
                    else:
                        pkg["structure_1h"] = "1H_SWING_CHOP"
        else:
            print(f"[AI Brain] ⚠️ {inst_id} 1H K线获取失败（{_candles_src} 未取回），1H ATR/RSI/结构字段降级缺省")
    except Exception as exc:
        print(f"[AI Brain] ⚠️ {inst_id} 1H K线处理异常: {exc}")

    # 4. 4H Candles (recent 16, about 64 hours) & 4H Macro Structure
    try:
        d = {"data": fetch_candles(inst_id, bar="4H", limit=16, venue=venue)}
        if d["data"]:
            raw_4h = d["data"]
            pkg["recent_4h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_4h[:8]]
            if len(raw_4h) >= 8:
                closes_4h = [float(c[4]) for c in reversed(raw_4h)]
                ma5_4h = sum(closes_4h[-5:]) / 5
                ma12_4h = sum(closes_4h[-12:]) / min(len(closes_4h), 12)
                if closes_4h[-1] > ma5_4h > ma12_4h:
                    pkg["macro_4h"] = "4H_MACRO_BULL (大级别多头通道)"
                elif closes_4h[-1] < ma5_4h < ma12_4h:
                    pkg["macro_4h"] = "4H_MACRO_BEAR (大级别空头承压)"
                else:
                    pkg["macro_4h"] = "4H_MACRO_RANGE (大级别区间震荡)"
        else:
            print(f"[AI Brain] ⚠️ {inst_id} 4H K线获取失败（{_candles_src} 未取回），4H 宏观结构字段降级缺省")
    except Exception as exc:
        print(f"[AI Brain] ⚠️ {inst_id} 4H K线处理异常: {exc}")

    # 5. Funding Rate & OI（决策链 venue 接线：统一走注入函数，不再硬编码 OKX URL）
    if item["type"] == "crypto":
        try:
            if fetch_funding_rate is not None:
                fr = fetch_funding_rate(inst_id, venue=venue)
                if fr is not None:
                    # fetch_funding_rate 返回值已是 ×100 的百分比（OKX/币安两侧一致），不再二次 ×100
                    pkg["fundingRate"] = round(float(fr), 4)
        except Exception as exc:
            note_failure("okx_funding_rate", exc)

        try:
            if fetch_open_interest is not None:
                oi = fetch_open_interest(inst_id, venue=venue)
                if isinstance(oi, dict):
                    usd = float(oi.get("oiUsd", 0) or 0)
                    if usd > 0:
                        pkg["oiUsd"] = f"{round(usd / 1e8, 2)}亿 U" if usd > 1e8 else f"{round(usd / 1e4, 1)}万 U"
        except Exception as exc:
            note_failure("okx_open_interest", exc)

        # lsRatio / takerNetUsd：决策链 venue 接线——币安走注入函数（数据门户等价口径），
        # OKX 走 Rubik urllib。注入函数为 None 时保持 "N/A"（fail-soft）。
        if ccy:
            # lsRatio：币安 → fetch_long_short_ratio；OKX → Rubik urllib
            if is_binance and fetch_long_short_ratio is not None:
                try:
                    _lsr = fetch_long_short_ratio(inst_id, venue=venue)
                    if _lsr is not None:
                        pkg["lsRatio"] = float(_lsr)
                except Exception as exc:
                    note_failure("binance_ls_ratio", exc)
            elif not is_binance:
                try:
                    req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m", headers=headers)
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        d = json.loads(resp.read().decode("utf-8"))
                        if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                            pkg["lsRatio"] = float(d["data"][0][1])
                except Exception as exc:
                    note_failure("okx_ls_ratio", exc)

            # takerNetUsd：币安 → fetch_taker_volume（单点 dict）；OKX → Rubik 序列
            if is_binance and fetch_taker_volume is not None:
                try:
                    _tv = fetch_taker_volume(inst_id, venue=venue)
                    if isinstance(_tv, dict):
                        b_vol = float(_tv.get("buyVol") or 0)
                        s_vol = float(_tv.get("sellVol") or 0)
                        net_diff = b_vol - s_vol
                        pkg["takerNetUsd"] = f"{round(net_diff / 1e4, 1)}万 U"
                except Exception as exc:
                    note_failure("binance_taker_volume", exc)
            elif not is_binance:
                try:
                    req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m", headers=headers)
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        d = json.loads(resp.read().decode("utf-8"))
                        if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                            b_vol = float(d["data"][0][1])
                            s_vol = float(d["data"][0][2])
                            net_diff = b_vol - s_vol
                            pkg["takerNetUsd"] = f"{round(net_diff / 1e4, 1)}万 U"
                except Exception as exc:
                    note_failure("okx_taker_volume", exc)

        # 6. ADX Trend Strength Indicator (1H) via direct REST（决策链 venue 接线：透传 venue）
        try:
            adx_data = fetch_single_indicator(inst_id, "ADX", bar="1H", venue=venue)
            if adx_data and "adx" in adx_data:
                pkg["adx_1h"] = float(adx_data.get("adx", 0.0) or 0.0)
        except Exception as exc:
            note_failure("okx_adx_1h", exc)

    # 六条判据**逐条**记账（2026-10-30 用户报「币安侧一律 数据质量 invalid」）：
    # 只报 invalid 等于没说 —— 看提示词的人无法判断是盘口缺失、还是某档 K 线不足、
    # 到底是缺几根。故把不满足的条目拼成 `data_quality_reason` 随包下传，
    # 由提示词渲染（`数据质量: invalid — 15m K线不足(6/12)`）。
    _missing_market_data = []
    if pkg["price"] <= 0:
        _missing_market_data.append("现价缺失")
    if pkg["bidPx"] <= 0:
        _missing_market_data.append("买一价缺失")
    if pkg["askPx"] < pkg["bidPx"]:
        _missing_market_data.append(f"盘口倒挂(bid={pkg['bidPx']}/ask={pkg['askPx']})")
    if len(pkg["recent_15m"]) < 12:
        _missing_market_data.append(f"15m K线不足({len(pkg['recent_15m'])}/12)")
    if len(pkg["recent_1h"]) < 8:
        _missing_market_data.append(f"1H K线不足({len(pkg['recent_1h'])}/8)")
    if len(pkg["recent_4h"]) < 6:
        _missing_market_data.append(f"4H K线不足({len(pkg['recent_4h'])}/6)")
    required_market_data = not _missing_market_data
    # 6. 已退役：微积分多周期引擎（2026-10 数理系统退场）。
    #    `pkg["calculus"]` 只保留**显式不可用**的默认占位 —— 键位不动，
    #    供 calculus_snapshot.json / 台账兜底读取链使用；**不再计算任何东西**。
    pkg["quant_factors"] = load_quant_factor_tiers(inst_id, venue=venue)
    pkg["data_quality"] = "valid" if required_market_data else "invalid"
    pkg["data_quality_reason"] = "、".join(_missing_market_data)

    # 标的体制状态（综合 4H 宏观、1H 结构与 1H ADX 趋势强度）
    m4h = str(pkg.get("macro_4h") or "")
    s1h = str(pkg.get("structure_1h") or "")
    adx_v = float(pkg.get("adx_1h", 0.0) or 0.0)
    if "BULL" in m4h and "BULL" in s1h and adx_v >= 20.0:
        pkg["market_regime"] = "STRONG_TREND_BULL"
    elif "BEAR" in m4h and "BEAR" in s1h and adx_v >= 20.0:
        pkg["market_regime"] = "STRONG_TREND_BEAR"
    elif "RANGE" in m4h or "CHOP" in s1h or (0.0 < adx_v < 20.0):
        pkg["market_regime"] = "CHOP_RANGE"
    elif m4h and s1h:
        pkg["market_regime"] = "TRANSITION"
    else:
        pkg["market_regime"] = None
    return pkg
