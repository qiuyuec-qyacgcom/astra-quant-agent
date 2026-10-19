"""Shared, validated ASTRA trading universe configuration."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
# 结构优化阶段 4·B3 第四十八刀：本地锁兜底外提到 `scripts/local_lock.py`。
# ⚠️ 双模导入：本模块既可能以裸名导入（`scripts/` 在 sys.path），
# 也可能以 `scripts.instrument_pool` 导入（repo 根在 sys.path）。
try:  # repo 根在 sys.path
    from scripts.local_lock import local_file_lock  # noqa: E402
except ImportError:  # scripts/ 在 sys.path
    from local_lock import local_file_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
#: ⚠️ 这些路径**必须**是模块级常量，不能写成函数内 `ROOT / "data" / …` 内联拼法。
#:
#: 原因（结构优化阶段 4·B3 第七十三刀实测确认）：测试沙箱
#: `tests/config_sandbox.py::isolate_config` 的重定向**只对模块级 UPPERCASE 常量
#: 生效**，而且要求该常量能 `relative_to(project / "data")` 成功。
#: 于是 `ROOT`（指向**仓库根**）本身永远不被重定向，而函数内的
#: `state_file = ROOT / "data" / "trading_state.json"` 又因是**局部变量**
#: 根本不在 `vars(module)` 里 —— 两者叠加的结果是：
#: **跑测试套件会直接覆盖生产 `data/trading_state.json`**（实测 mtime 落在
#: 套件运行窗口内，且当时无任何 trader 周期）。
#:
#: 这与第四十八刀那次事故（`instrument_pool.json` 被写成缺 `instId`/`ctVal`
#: 导致实盘周期 fail-safe）是**同一机制**。`POOL_FILE` 当初就是模块级常量，
#: 所以它一直是安全的；其余四个是内联拼法，一直漏。本刀把它们统一提上来。
DATA_DIR = ROOT / "data"
POOL_FILE = DATA_DIR / "instrument_pool.json"
#: 币安独立标的池（2026-10 双所标的池）。
#:
#: 为什么不共用 `instrument_pool.json`：两所**数量语义根本不同**——
#:   · OKX 走 contracts（整数张），每张名义 = price × ctVal（BTC ctVal=0.01）；
#:   · 币安走 base_asset（币数），ctVal 恒 1（见 `exchanges/binance.py::instruments`）。
#: 同一份 `ctVal` 喂给两边必然让币安下单量放大 100 倍（BTC）——这是资金级事故，
#: 故币安池必须是**独立文件、独立规格**，绝不复用 OKX 的池条目。
BINANCE_POOL_FILE = DATA_DIR / "instrument_pool_binance.json"
TRADING_STATE_FILE = DATA_DIR / "trading_state.json"
FACTOR_LIBRARY_FILE = DATA_DIR / "factor_library_snapshot.json"
NEWS_SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
DASHBOARD_CACHE_FILE = DATA_DIR / "dashboard_last_good.json"

TIER_PROFILES = {
    "tier_1_bluechip": {
        "label": "蓝筹主流",
        "max_leverage": 5,
        "base_risk_ratio": 1.0,
        "sl_atr_mult": 1.8,
        "min_vol_24h_usd": 100_000_000,
    },
    "tier_2_momentum": {
        "label": "高弹性动量",
        "max_leverage": 3,
        "base_risk_ratio": 0.75,
        "sl_atr_mult": 2.2,
        "min_vol_24h_usd": 20_000_000,
    }
}

# 出厂默认标的池（2026-10-19 收敛：双所各保留 BTC 单一标的）。
#
# 用户明确要求「分两套标的池，币安默认一个 BTC，OKX 去除 BTC 以外的标的」：
#   两所均只保留 BTC-USDT-SWAP 作为出厂默认。其余标的不再进入默认池，
#   需要时由管理员在「标的池」页签手动增补（增补后落在 data/*.json，被 .gitignore 忽略）。
#
# ⚠️ 这份常量是「默认标的」**唯一可持久化**的定义：`data/instrument_pool.json`
# 被 `.gitignore` 忽略（`data/*.json`），池文件缺失/损坏时 `load_instruments()`
# 走的就是这份清单。故它必须与运行态池**保持同一份 1 条**，否则一旦重建就会复活
# 已淘汰标的并把并发上限顶高。
# 规格取自 OKX /public/instruments 实时数据；BTC 为 Tier-1 蓝筹（兼全局黑天鹅哨兵基准）。
# 扩容说明：MAX_CONCURRENT_POSITIONS = len(池) 自动跟随（当前 1）。
DEFAULT_INSTRUMENTS = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "ccy": "BTC", "tier": "tier_1_bluechip", "max_leverage": 12, "sl_atr_mult": 1.8, "base_sz": 1, "precision": 1, "ctVal": 0.01, "tickSz": "0.1", "minSz": "0.01", "risk_per_trade_usd": 0.0},
]


def evaluate_instrument_tier(inst_id: str, name: str = "") -> str:
    """Classify instrument into Tier-1 Bluechip or Tier-2 Momentum."""
    name_upper = (name or inst_id.split("-")[0]).upper()
    if name_upper in ("BTC", "ETH"):
        return "tier_1_bluechip"
    return "tier_2_momentum"


def derive_instrument_leverage_cap(
    tier: str,
    min_leverage: float | None = None,
    max_leverage: float | None = None,
) -> int:
    """根据标的分层 (Tier 1 蓝筹 vs Tier 2 动量) 与全局风控杠杆区间动态派生单标的上限。

    - Tier 1 (蓝筹 BTC/ETH)：跟随全局上限 MAX_LEVERAGE；
    - Tier 2 (动量高弹性 SOL/DOGE 等)：在 [MIN_LEVERAGE, MAX_LEVERAGE] 区间内按风险梯度收紧，
      保证不低于 MIN_LEVERAGE 且不高于 MAX_LEVERAGE。
    """
    if min_leverage is None or max_leverage is None:
        try:
            from scripts.risk_constants import MIN_LEVERAGE as _RC_MIN, MAX_LEVERAGE as _RC_MAX
        except Exception:
            _RC_MIN, _RC_MAX = 2.0, 5.0
        if min_leverage is None:
            min_leverage = float(os.getenv("ASTRA_MIN_LEVERAGE", "") or _RC_MIN or 2.0)
        if max_leverage is None:
            max_leverage = float(os.getenv("ASTRA_MAX_LEVERAGE", "") or _RC_MAX or 5.0)
    lo = max(1.0, float(min_leverage or 2.0))
    hi = max(lo, float(max_leverage or 5.0))
    if tier == "tier_1_bluechip":
        return max(1, int(round(hi)))
    # 动量币在 [lo, hi] 内取约 40% 的弹性跨度（在默认 2~5x 时刚好为 3x，与出厂基线完美对齐）
    cap = int(round(lo + (hi - lo) * 0.4))
    return max(int(round(lo)), min(int(round(hi)), cap))


def sync_pool_leverage_caps(
    min_leverage: float | None = None,
    max_leverage: float | None = None,
) -> list[dict[str, Any]]:
    """根据指定的杠杆区间，在锁内重新对齐池内所有标的的 max_leverage 并安全落盘。"""
    def _updater(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in pool:
            tier = item.get("tier") or evaluate_instrument_tier(item.get("instId", ""), item.get("name", ""))
            item["tier"] = tier
            item["max_leverage"] = derive_instrument_leverage_cap(
                tier, min_leverage=min_leverage, max_leverage=max_leverage
            )
        return pool
    return mutate_instruments(_updater)


def score_universe_candidate(
    candidate: dict[str, Any],
    vol_24h_usd: float = 0.0,
    atr_pct: float = 0.0,
    funding_rate: float = 0.0
) -> dict[str, Any]:
    """Evaluate candidate instrument suitability and rank quality score (0 ~ 100)."""
    name = candidate.get("name", "")
    tier = evaluate_instrument_tier(candidate.get("instId", ""), name)
    profile = TIER_PROFILES[tier]

    score = 50.0
    # Liquidity check
    if vol_24h_usd > 0:
        if vol_24h_usd >= profile["min_vol_24h_usd"]:
            score += 20.0
        else:
            score -= 30.0

    # Volatility band check (healthy swing trading band: 1.5% ~ 6.0%)
    if atr_pct > 0:
        if 1.5 <= atr_pct <= 6.0:
            score += 20.0
        elif atr_pct < 1.0:
            score -= 15.0  # too sleepy
        elif atr_pct > 9.0:
            score -= 25.0  # extreme rug risk

    # Extreme funding rate penalty (abs(funding) > 0.05% implies crowding)
    if abs(funding_rate) > 0.0005:
        score -= 15.0

    candidate["tier"] = tier
    candidate["max_leverage"] = derive_instrument_leverage_cap(tier)
    candidate["sl_atr_mult"] = profile["sl_atr_mult"]
    candidate["universe_score"] = round(max(0.0, min(100.0, score)), 1)
    return candidate


def _precision(tick_size: str) -> int:
    normalized = tick_size.rstrip("0")
    return len(normalized.split(".", 1)[1]) if "." in normalized else 0


def from_okx_instrument(raw: dict[str, Any]) -> dict[str, Any]:
    inst_id = str(raw.get("instId", "")).upper()
    base = str(raw.get("baseCcy") or inst_id.split("-", 1)[0]).upper()
    tick_size = str(raw.get("tickSz") or "0.0001")
    tier = evaluate_instrument_tier(inst_id, base)
    profile = TIER_PROFILES[tier]
    return {
        "instId": inst_id,
        "name": base,
        "type": "crypto",
        "ccy": base,
        "tier": tier,
        "max_leverage": derive_instrument_leverage_cap(tier),
        "sl_atr_mult": profile["sl_atr_mult"],
        "base_sz": 1,
        "precision": _precision(tick_size),
        "ctVal": float(raw.get("ctVal") or 1.0),
        "tickSz": tick_size,
        "minSz": str(raw.get("minSz") or "1"),
        "risk_per_trade_usd": 0.0,
    }


def from_binance_instrument(raw: dict[str, Any]) -> dict[str, Any]:
    """币安 USDⓈ-M 永续 exchangeInfo 行 → 池条目。

    与 OKX 版的**关键差异**：`ctVal` 恒 1（币安按 base_asset 币数下单，无张数概念），
    `precision` 取 lotSz/stepSize 小数位。若此处误用 OKX 的张数语义，下单量会按
    1/ctVal 放大（BTC 即 100 倍）——故两所转换函数**故意不合并**。
    """
    inst_id = str(raw.get("instId", "")).upper()
    base = str(raw.get("baseCcy") or inst_id.split("-", 1)[0]).upper()
    tick_size = str(raw.get("tickSz") or "0.0001")
    # 币安同时给 lotSz（数量步长）与 minSz；精度以数量步长为准（OKX 版以价格步长为准，
    # 因为 OKX 下的是整数张，价格精度即 tickSz 的位数）。
    step = str(raw.get("lotSz") or raw.get("stepSize") or raw.get("minSz") or "1")
    tier = evaluate_instrument_tier(inst_id, base)
    profile = TIER_PROFILES[tier]
    return {
        "instId": inst_id,
        "name": base,
        "type": "crypto",
        "ccy": base,
        "tier": tier,
        "max_leverage": derive_instrument_leverage_cap(tier),
        "sl_atr_mult": profile["sl_atr_mult"],
        "base_sz": 1,
        "precision": _precision(step),
        "ctVal": 1.0,
        "tickSz": tick_size,
        "minSz": str(raw.get("minSz") or step),
        "risk_per_trade_usd": 0.0,
    }


def from_instrument(venue: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """按场所分派到对应的规格转换（唯一入口，避免调用方各写一份 if venue）。"""
    return from_binance_instrument(raw) if normalize_venue(venue) == "binance" else from_okx_instrument(raw)


REQUIRED_POOL_FIELDS = ("instId", "name", "ctVal")

#: 币安出厂默认池——**必须与 OKX 默认池币种一致**：双所是同一策略的两套执行腿，
#: 池子币种不同会让"切换交易所"直接改变策略覆盖面（属于策略变更而非切所）。
#: 2026-10-19 收敛：与 OKX 池一致，仅保留 BTC 单一标的。
#: 规格（precision/tickSz/minSz）取自币安 USDⓈ-M 永续，**ctVal 恒 1、按币数下单**，
#: precision 为该所最小下单量 stepSize 的小数位（币安 BTC 永续为 0.001 ⇒ 3）。
BINANCE_DEFAULT_INSTRUMENTS = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "ccy": "BTC", "tier": "tier_1_bluechip", "max_leverage": 12, "sl_atr_mult": 1.8, "base_sz": 1, "precision": 3, "ctVal": 1.0, "tickSz": "0.1", "minSz": "0.001", "risk_per_trade_usd": 0.0},
]


def default_instruments_for(venue: Any = None) -> list[dict[str, Any]]:
    """该场所的出厂默认池（缺失/损坏时的兜底，仅供展示，交易侧不开新仓）。"""
    return BINANCE_DEFAULT_INSTRUMENTS if normalize_venue(venue) == "binance" else DEFAULT_INSTRUMENTS

# 审计 P2-11(2026-09-13)：池文件坏掉时旧实现静默返回 10 币 DEFAULT_INSTRUMENTS ——
# 于是管理员删掉的标的会因为"文件坏了"重新出现在交易池里（缺失≠0 的反面：坏掉≠默认）。
# 现在把状态记在这里，交易侧据此 fail-closed（只做风控接管、不开新仓），而
# dashboard/后端仍能读到一份可展示的数据，不至于整个 API 起不来。
_POOL_STATE: Dict[str, Any] = {"status": "unknown", "detail": "", "dropped": []}

#: 支持独立标的池的场所（顺序即前端展示顺序）。
#: 新增场所需同时提供 `*_DEFAULT_INSTRUMENTS` 与规格换算，否则不得登记。
POOL_VENUES = ("okx", "binance")
DEFAULT_VENUE = "okx"


def normalize_venue(venue: Any) -> str:
    """非法/空/`auto` → 默认 OKX。

    `auto` 必须归一：`routing_policy` 在"未手选"时返回的是字符串 `"auto"`
    （评分路由语义），它不是交易所名。若放行，"auto" 会被当成一个池子去读
    `instrument_pool.json` 之外的路径，交易侧就会拿到一份空池。
    """
    key = str(venue or "").strip().lower()
    return key if key in POOL_VENUES else DEFAULT_VENUE


def pool_file_for(venue: Any = None) -> Path:
    """目标场所的池文件。OKX 沿用原文件，币安用独立文件（见 BINANCE_POOL_FILE 注释）。"""
    return BINANCE_POOL_FILE if normalize_venue(venue) == "binance" else POOL_FILE


def pool_state() -> Dict[str, Any]:
    """最近一次 load_instruments() 的可信度快照（status: ok|missing|corrupt|empty|invalid）。"""
    return {**_POOL_STATE, "dropped": list(_POOL_STATE.get("dropped") or [])}


def pool_is_trustworthy() -> bool:
    """true 仅当 POOL_FILE 存在且解析出一份通过字段校验的非空池。"""
    return _POOL_STATE.get("status") == "ok"


def _validate_pool_items(instruments: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """逐项校验：缺必需字段/类型不对的条目丢弃并点名（旧实现是周期中途 KeyError 崩掉）。"""
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for index, item in enumerate(instruments):
        if not isinstance(item, dict):
            dropped.append(f"#{index}({type(item).__name__})")
            continue
        missing = [field for field in REQUIRED_POOL_FIELDS if not str(item.get(field) or "").strip()]
        if missing:
            dropped.append(f"#{index} {item.get('instId') or item.get('name') or '?'} 缺 {','.join(missing)}")
            continue
        try:
            float(item.get("ctVal"))
        except (TypeError, ValueError):
            dropped.append(f"#{index} {item.get('instId')} 的 ctVal 非数值（{item.get('ctVal')!r}）")
            continue
        kept.append(item)
    return kept, dropped


def active_pool_venue() -> str:
    """当前**生效**的标的池所属交易所（`data/venue_routing.json` 的 preferred_venue）。

    这是双所标的池的单一判定源：页头切换器写入它，交易进程、后台路由、面板全部
    据此取池——因此"切换后自动使用切换后的标的池"是**读时生效**的，
    不存在"某个进程还抱着旧池"的缝隙。读取失败一律回退 okx（fail-safe，与
    routing_policy 的缺省语义一致）。
    """
    try:
        from astra_backend.exchanges.routing_policy import load_preferred_venue
        return normalize_venue(load_preferred_venue())
    except Exception:
        pass
    # 后端不在 sys.path 的纯脚本场景：从 routing_policy 取**同一个**文件常量
    # （单一事实源），而不是在 scripts/ 里另拼一份路径字面量。
    try:
        from astra_backend.exchanges import routing_policy as _rp
        raw = json.loads(Path(_rp.ROUTING_FILE).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return normalize_venue(raw.get("preferred_venue"))
    except Exception:
        pass
    return DEFAULT_VENUE


def load_instruments(venue: Any = None) -> list[dict[str, Any]]:
    """读取指定场所的标的池。

    `venue` 缺省 = **当前生效交易所**（不是固定 OKX）。这一改动是"切所即换池"的关键：
    交易进程启动/每个周期调 `load_instruments()` 不传参，就会自然跟随页头切换结果。
    需要固定某所时显式传 `venue="okx"` / `venue="binance"`。
    """
    _venue = normalize_venue(venue or active_pool_venue())
    _pool_file = pool_file_for(_venue)
    _defaults = default_instruments_for(_venue)
    if not _pool_file.exists():
        # 首次启动没有池文件：用出厂默认并把状态标成 missing（交易侧不据此开新仓）
        _POOL_STATE.update({"status": "missing", "detail": f"{_pool_file} 不存在，已按出厂默认池返回", "dropped": []})
        print(f"[instrument_pool] warn 未找到 {_pool_file}，返回 {_venue} 出厂默认 {len(_defaults)} 币；"
              f"交易侧本轮不开新仓（请先在后台保存一次标的池）")
        return [dict(item) for item in _defaults]
    try:
        payload = json.loads(_pool_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _POOL_STATE.update({"status": "corrupt", "detail": f"{_pool_file} 解析失败: {exc}", "dropped": []})
        print(f"[instrument_pool] error 标的池文件损坏（{exc}）→ 已退回出厂默认清单仅供展示，"
              f"交易侧本轮不开新仓。请修复或重新保存标的池。")
        return [dict(item) for item in _defaults]
    instruments = payload.get("instruments", payload) if isinstance(payload, dict) else payload
    if not isinstance(instruments, list) or not instruments:
        _POOL_STATE.update({"status": "empty", "detail": f"{_pool_file} 里没有 instruments", "dropped": []})
        print(f"[instrument_pool] error 标的池为空（{_pool_file}）→ 交易侧本轮不开新仓")
        return [dict(item) for item in _defaults]
    kept, dropped = _validate_pool_items(instruments)
    if dropped:
        print(f"[instrument_pool] error 标的池有 {len(dropped)} 项非法，已丢弃: {', '.join(dropped)}")
    if not kept:
        _POOL_STATE.update({"status": "invalid", "detail": f"{_pool_file} 全部条目非法", "dropped": dropped})
        print(f"[instrument_pool] error 标的池无一条合法 → 交易侧本轮不开新仓")
        return [dict(item) for item in _defaults]
    try:
        from scripts.risk_constants import MIN_LEVERAGE as _RC_MIN, MAX_LEVERAGE as _RC_MAX
    except Exception:
        _RC_MIN, _RC_MAX = 2.0, 5.0
    _cur_min = float(os.getenv("ASTRA_MIN_LEVERAGE", "") or _RC_MIN or 2.0)
    _cur_max = float(os.getenv("ASTRA_MAX_LEVERAGE", "") or _RC_MAX or 5.0)
    if _cur_min > _cur_max:
        _cur_min = _cur_max

    for item in kept:
        if "tier" not in item:
            item["tier"] = evaluate_instrument_tier(item.get("instId", ""), item.get("name", ""))
            item["sl_atr_mult"] = TIER_PROFILES[item["tier"]]["sl_atr_mult"]
        tier = item["tier"]
        cur_cap = item.get("max_leverage")
        # 兼容自适应：若池内上限低于当前全局下限、或高于全局上限、或蓝筹未跟随全局上限，按当前风控区间派生
        if cur_cap is None or cur_cap < _cur_min or cur_cap > _cur_max or (tier == "tier_1_bluechip" and cur_cap != int(round(_cur_max))):
            item["max_leverage"] = derive_instrument_leverage_cap(tier, min_leverage=_cur_min, max_leverage=_cur_max)
    _POOL_STATE.update({
        "status": "ok" if not dropped else "invalid",
        "detail": "" if not dropped else f"丢弃 {len(dropped)} 项: {', '.join(dropped)}",
        "dropped": dropped,
    })
    return kept


def _pool_lock(venue: Any = None):
    """跨进程互斥（审计 P2-6）：池文件是多进程 RMW 目标（后台路由写、采集脚本写）。
    优先用 astra_backend.file_locks（可重入、锁文件同目录），后端不在路径时退化为
    本地 flock —— 绝不在"锁不可用"时静默放行。

    兜底实现已移到 `scripts/local_lock.py`（结构优化阶段 4·B3 第四十八刀）——
    原手写兜底**不可重入**，而 `mutate_instruments` 会在锁内调
    `save_instruments`（嵌套取锁），走兜底分支会同线程自锁挂死。

    双所改造：锁按**目标池文件**取（file_lock/cwd 均以文件为键），
    因而 OKX 与币安两份池可并行写入，互不阻塞。
    """
    _pool_file = pool_file_for(venue or active_pool_venue())
    try:
        from astra_backend.file_locks import file_lock
        return file_lock(_pool_file)
    except Exception:
        return local_file_lock(_pool_file)


def mutate_instruments(mutator, venue: Any = None):
    """在锁内完成 load → mutate → save 的整段 RMW（审计 P2-6）。

    旧实现里路由各自 `current = load_instruments(); save_instruments([...])`——
    两个并发保存（两次点击、页面重试、采集脚本同时跑）基于同一份旧池回写，后写者
    静默吞掉先写者（丢标的/丢参数）。mutator 接收当前池列表，返回要落盘的新列表。"""
    _venue = normalize_venue(venue or active_pool_venue())
    with _pool_lock(_venue):
        current = load_instruments(_venue)
        updated = mutator([dict(item) for item in current])
        if updated is None:
            return current
        save_instruments(list(updated), venue=_venue)
        return updated


def save_instruments(instruments: list[dict[str, Any]], venue: Any = None) -> None:
    _venue = normalize_venue(venue or active_pool_venue())
    with _pool_lock(_venue):
        _write_pool_file(instruments, venue=_venue)


def _write_pool_file(instruments: list[dict[str, Any]], venue: Any = None) -> None:
    _venue = normalize_venue(venue or active_pool_venue())
    _pool_file = pool_file_for(_venue)
    _pool_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".instrument-pool-", suffix=".tmp", dir=_pool_file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "venue": _venue, "instruments": instruments}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, _pool_file)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    try:
        sync_instruments_state(_venue)
    except Exception:
        pass


def _write_json_atomic(path, payload: Any) -> None:
    """审计③(2026-09-13)：同步扇出的三个下游文件曾直 write_text——与 trader 周期
    整档写者并存时，并发读者（面板等）可撞半截 JSON。统一 mkstemp+fsync+replace
    （与 save_instruments 同款路数）。双写者『丢更新』的单写者协议属批4结构收口。"""
    p = Path(path)
    fd, temp_path = tempfile.mkstemp(prefix="." + p.name + "-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, p)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _run_captured(script, label=None, timeout=45, env=None):
    """审计(2026-09-13)：同解释器子进程 + 非零必吼（旧裸 python3 shell 串=静默死亡）。"""
    from astra_backend.spawn import run_script
    return run_script(script, timeout=timeout, label=label, env=env)


def sync_instruments_state(venue: Any = None) -> None:
    """Synchronize trading_state.json, factor_library_snapshot.json, news_sentiment.json,
    and dashboard cache when the trading instrument pool changes.

    双所改造（2026-10）：只有**当前生效交易所**的池变更才允许扇出到下游。
    下游文件（trading_state / factor / news / dashboard cache）是全局单份，
    若币安池保存也去扇出，就会把正在跑的 OKX 池顶掉——即用户明确要求的
    「切换后不走切换前的标的池」的反面。判断依据是 `venue_routing.json`
    的 `preferred_venue`（当前执行所）。
    """
    active_venue = active_pool_venue()
    # 空 venue = "保存的就是生效池"（老调用方不带场所）；非空则必须等于生效所才扇出
    changed_venue = normalize_venue(venue) if venue else active_venue
    if changed_venue != active_venue:
        print(f"[instrument_pool] skip 同步下游：本次变更场所={changed_venue}，"
              f"当前生效场所={active_venue}（非生效场所的池只落盘，不扇出）")
        return

    active_pool = load_instruments(active_venue)
    active_ids = {item["instId"] for item in active_pool}
    active_names = {item["name"] for item in active_pool}

    # 1. Update data/trading_state.json
    state_data: dict[str, Any] = {}
    if TRADING_STATE_FILE.exists():
        try:
            state_data = json.loads(TRADING_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            state_data = {}

    current_insts = state_data.get("instruments", [])
    existing_by_id = {ins.get("instId"): ins for ins in current_insts if isinstance(ins, dict) and ins.get("instId")}

    new_insts = []
    for target in active_pool:
        inst_id = target["instId"]
        if inst_id in existing_by_id:
            new_insts.append(existing_by_id[inst_id])
        else:
            # New coin baseline
            # ★ 2026-10「不许假数据」：新币基线**只写系统状态**（"观望/等功能接入"），
            #   **不写任何市场观测值**。原实现写入 rsi=50.0 / vol_ratio=1.0 /
            #   obv_flow="NEUTRAL" / market_regime="CHOP" / trend="震荡" —— 看板会把
            #   它们显示成"RSI 中性、量能正常、区间震荡"，而事实是这个币还没取过数。
            #   市场类字段一律缺失（`None` / `"--"`），由行情与因子引擎随后填充。
            new_insts.append({
                "name": target.get("name"),
                "instId": inst_id,
                "type": target.get("type", "crypto"),
                "price": "--",
                "rsi": None,
                "rsi_7": None,
                "vwap_bias": None,
                "macd_hist": None,
                "macd_accel": None,
                "obv_flow": None,
                "bb_bandwidth": None,
                "vol_ratio": None,
                "market_regime": None,
                "structure_1h": None,
                "trend_1h": None,
                "trend_4h": None,
                "score": 0.0,
                "action": "WAIT",
                "strategy": "⚪ 观望",
                "desc": "新配置资产，微结构与特征雷达已初始化",
                "position": None,
            })
    state_data["instruments"] = new_insts
    state_data["max_positions"] = len(active_pool)
    try:
        _write_json_atomic(TRADING_STATE_FILE, state_data)
    except Exception:
        pass

    # 2. Update data/factor_library_snapshot.json to prune deleted coins
    if FACTOR_LIBRARY_FILE.exists():
        try:
            factor_data = json.loads(FACTOR_LIBRARY_FILE.read_text(encoding="utf-8"))
            if isinstance(factor_data, dict) and "instruments" in factor_data:
                factor_data["instruments"] = [
                    item for item in factor_data["instruments"]
                    if isinstance(item, dict) and item.get("instId") in active_ids
                ]
                _write_json_atomic(FACTOR_LIBRARY_FILE, factor_data)
        except Exception:
            pass

    # 3. Update data/news_sentiment.json to prune deleted coins and ensure active coins
    if NEWS_SENTIMENT_FILE.exists():
        try:
            news_data = json.loads(NEWS_SENTIMENT_FILE.read_text(encoding="utf-8"))
            if isinstance(news_data, dict) and "coins_sentiment" in news_data:
                coins_dict = news_data["coins_sentiment"]
                cleaned_coins = {c: s for c, s in coins_dict.items() if c in active_names}
                for name in active_names:
                    if name not in cleaned_coins:
                        cleaned_coins[name] = {
                            "ccy": name,
                            "label": "neutral",
                            "bullish_ratio": "50.0%",
                            "bearish_ratio": "50.0%",
                            "bullish_pct": "50.0%",
                            "bearish_pct": "50.0%",
                            "long_short_ratio": "1.00",
                            "bull_cnt": 0,
                            "bear_cnt": 0,
                            "neutral_cnt": 0,
                            "mentions": 0,
                            "sentiment_factor_score": 0.0,
                        }
                news_data["coins_sentiment"] = cleaned_coins
                _write_json_atomic(NEWS_SENTIMENT_FILE, news_data)
        except Exception:
            pass

    # 4. Invalidate dashboard cache file so next fetch generates fresh state
    if DASHBOARD_CACHE_FILE.exists():
        try:
            DASHBOARD_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # 5. Run factor_library and news_sentiment in a non-blocking background thread
    import subprocess
    import threading
    # ⚠️ 第七十六刀：**线程启动前**抓环境快照。
    # 测试沙箱（isolate_config）的 cleanup 只保证在测试方法结束时还原 ——
    # 后台线程真正走到 spawn 可能在那之后，"继承当前环境"就会拿到
    # **已还原的干净环境** ⇒ 子进程写生产 data/（§88 实测窗口）。
    # 快照在调用线程前同步抓取，生产里快照=真实环境（行为不变）。
    _env_snapshot = dict(os.environ)
    def _run_bg() -> None:
        try:
            fl_script = ROOT / "scripts" / "factor_library.py"
            if fl_script.exists():
                _run_captured(fl_script, env=_env_snapshot)
            nh_script = ROOT / "scripts" / "news_sentiment_harvester.py"
            if nh_script.exists():
                _run_captured(nh_script, env=_env_snapshot)
        except Exception:
            pass
    threading.Thread(target=_run_bg, daemon=True).start()

