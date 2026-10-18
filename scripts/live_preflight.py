#!/usr/bin/env python3
"""实盘切换前的**只读**体检（阶段 0）—— **绝不下一单**。

## 为什么需要这个脚本

本系统对 OKX 账户存在若干**隐式前提**，而这些前提**代码里一律不校验**，
只会在真正下单的那一刻以 OKX 错误码的形式炸出来。2026-09-25 的实盘复盘
把其中三个坑连着踩了一遍：

| 运维看到的 | 真实原因 | 谁最先告诉你 |
|---|---|---|
| `51010 账户模式不支持` | 账户停在「简单交易模式」(acctLv=1) | 只有下单一刻 |
| `51000 Parameter posSide error` | 持仓模式是单向 net | 2026-09-25 起有持仓模式闸 |
| `50123 该 Key 无此币种交易权限` | 白名单未含**计价币 USDT** | 只有下单一刻 |

所以切换 live 之前，先用**只读接口**把它们核一遍。本脚本只发 GET 请求：
不产生任何委托、不修改任何账户设置。

## 用法

    python3 scripts/live_preflight.py                 # 体检当前生效环境
    python3 scripts/live_preflight.py --env live      # 显式体检 live（不改任何开关）
    python3 scripts/live_preflight.py --inst ETH-USDT-SWAP

退出码：0 = 全部通过；1 = 存在**阻塞项**（不要切 live）；2 = 仅告警项。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import okx_rest, okx_runtime  # noqa: E402

#: acctLv → 人读名称。四种账户模式里 **1（简单交易）装不下本系统的合约下单载荷**
#: （实盘实测 51010 账户模式不支持），其余三种均可用。
ACCOUNT_LEVEL_DESCRIPTIONS = {
    "1": "简单交易模式（⚠️ 不支持本系统合约下单载荷）",
    "2": "单币种保证金模式",
    "3": "跨币种保证金模式",
    "4": "组合保证金模式",
}

#: 本系统声明为「准入」的持仓模式，与 `astra_backend/exchanges/okx.py` 的
#: `entry_ready_position_modes` **同源**。net 模式下带 `posSide=long` 会被 OKX 拒。
ENTRY_READY_POSITION_MODES = ("long_short",)

VERDICT_OK = "PASS"
VERDICT_WARN = "WARN"
VERDICT_BLOCK = "BLOCK"


def _resolve_environment(requested_mode: str | None) -> okx_runtime.OKXEnvironment:
    """取生效环境；`requested_mode` 只在**本次进程内**覆盖 `ASTRA_OKX_ENV`。

    ⚠️ 必须先把 `_load_dotenv()` 的结果拿出来再覆盖：那一层会把**加密凭据库**
    合并进来，而 `selected_environment(values)` 只在 `values is None` 时才读它。
    直接传 `{"ASTRA_OKX_ENV": "live"}` 会导致**读不到任何凭据**（本脚本第一版即如此）。
    """
    merged = okx_runtime._load_dotenv()
    if requested_mode:
        merged["ASTRA_OKX_ENV"] = requested_mode
    return okx_runtime.selected_environment(merged)


def _get(path: str, environment, params: dict | None = None):
    """只读 GET。返回 `(ok, data_or_error)`；**绝不**发任何写请求。"""
    try:
        payload = okx_rest.request("GET", path, params or {}, env=environment)
    except Exception as exc:                       # 含 50123/50111 等业务拒绝
        return False, str(exc)
    data = payload.get("data") if isinstance(payload, dict) else payload
    return True, data


def check_credentials(environment) -> tuple[str, str]:
    if not environment.configured:
        return (VERDICT_BLOCK,
                f"{environment.mode.upper()} 凭据**不完整**（需要 API_KEY/SECRET_KEY/"
                "PASSPHRASE 三件套齐全）——系统会判定 NOT READY 并拒绝交易")
    return (VERDICT_OK,
            f"凭据齐备（来源 {environment.source}，指纹 {environment.fingerprint}）")


def check_account_config(environment) -> tuple[str, str, dict]:
    """`GET /api/v5/account/config` → 账户模式 + 持仓模式 + Key 权限。"""
    ok, data = _get("/api/v5/account/config", environment)
    if not ok:
        return (VERDICT_BLOCK, f"读取账户配置失败：{data}", {})
    row = (data or [{}])[0] if isinstance(data, list) else {}
    if not isinstance(row, dict) or not row:
        return (VERDICT_BLOCK, f"账户配置回包无法解析：{data!r}", {})
    account_level = str(row.get("acctLv") or "")
    position_mode = str(row.get("posMode") or "")
    permissions = str(row.get("perm") or "")
    settle_currency = str(row.get("settleCcy") or "")
    details = {
        "acctLv": account_level, "posMode": position_mode,
        "perm": permissions, "settleCcy": settle_currency,
    }
    # 账户模式：只有 1（简单交易）装不下合约下单载荷
    if account_level == "1":
        return (VERDICT_BLOCK,
                f"账户模式 acctLv=1 是{ACCOUNT_LEVEL_DESCRIPTIONS['1']}——"
                "请在 OKX 侧切到「单币种保证金」或更高后再切 live", details)
    if account_level not in ACCOUNT_LEVEL_DESCRIPTIONS:
        return (VERDICT_WARN, f"账户模式 acctLv={account_level!r} 无法识别，请人工确认",
                details)
    # 持仓模式：net 下 posSide 载荷会被 OKX 拒（本系统也不会准入）
    if position_mode and position_mode not in (
            "long_short_mode", "net_mode"):
        return (VERDICT_WARN, f"持仓模式 posMode={position_mode!r} 无法识别，请人工确认",
                details)
    if position_mode == "net_mode":
        return (VERDICT_BLOCK,
                "持仓模式是**单向(net_mode)**，而本系统只在 "
                f"{'/'.join(ENTRY_READY_POSITION_MODES)} 下核验过下单与保护腿载荷——"
                "持仓模式闸会禁新开仓。请在 OKX 侧改为「双向持仓」", details)
    if not position_mode:
        return (VERDICT_BLOCK, "回包里没有 posMode 字段，无法确认持仓模式", details)
    # Key 权限
    permission_parts = {p.strip() for p in permissions.split(",") if p.strip()}
    if "trade" not in permission_parts:
        return (VERDICT_BLOCK,
                f"Key 权限 perm={permissions!r} 不含 trade —— "
                "该 Key 只能读，无法下单", details)
    # ⚠️ 汇总口径必须**先落定**，再按需降级为 WARN —— 否则一旦命中告警就
    # 提前 return，会把 acctLv/posMode 这些**关键判据**整个吞掉（本脚本第一版
    # 实测就是这样：只报了"含提币"，运维看不到账户模式到底是什么）。
    summary = (f"{ACCOUNT_LEVEL_DESCRIPTIONS.get(account_level, account_level)}｜"
               f"持仓模式 {position_mode}｜权限 {permissions}｜"
               f"结算币 {settle_currency or '未回报'}")
    if "withdraw" in permission_parts:
        return (VERDICT_WARN,
                f"{summary}\n    ⚠️ 该 Key **包含提币权限** —— 违反最小权限原则。"
                "实盘 Key 建议只给「读取 + 交易」，绝不勾提币", details)
    return (VERDICT_OK, summary, details)


def check_balance(environment) -> tuple[str, str]:
    """`GET /api/v5/account/balance` → 权益（只读，用于核对资金规模）。"""
    ok, data = _get("/api/v5/account/balance", environment)
    if not ok:
        return (VERDICT_WARN, f"读取余额失败：{data}")
    row = (data or [{}])[0] if isinstance(data, list) else {}
    total_equity = row.get("totalEq") if isinstance(row, dict) else None
    if total_equity in (None, ""):
        return (VERDICT_WARN, f"余额回包里没有 totalEq：{data!r}")
    return (VERDICT_OK, f"账户总权益 {total_equity} USDT（请据此复核风控上限）")


def check_open_positions(environment) -> tuple[str, str]:
    """`GET /api/v5/account/positions` → 既有持仓（切换前必须心里有数）。"""
    ok, data = _get("/api/v5/account/positions", environment, {"instType": "SWAP"})
    if not ok:
        return (VERDICT_WARN, f"读取持仓失败：{data}")
    rows = data if isinstance(data, list) else []
    open_rows = [r for r in rows if isinstance(r, dict)
                 and abs(float(r.get("pos") or 0)) > 0]
    if not open_rows:
        return (VERDICT_OK, "无既有持仓（干净起点）")
    described = ", ".join(
        f"{r.get('instId')} {r.get('posSide')} {r.get('pos')}" for r in open_rows[:8])
    return (VERDICT_WARN,
            f"**已有 {len(open_rows)} 笔持仓**：{described} —— "
            "请确认这些仓是否在本系统的管辖范围内（否则会被己仓对账判为外部仓）")


def check_trade_permission_for_instrument(environment, inst_id: str) -> tuple[str, str]:
    """用**只读**的 `max-size` 端点探测该合约的交易权限。

    ⚠️ 这是**尽力而为**的探测：OKX 对「币种白名单」的拒绝主要发生在**下单**时，
    只读端点未必回报 50123。因此：
    - 回报 50123 ⇒ 白名单**确实**不含该合约的计价币，切 live 必炸；
    - 回报正常 ⇒ **不能**证明下单一定通过（可能仍有白名单限制）。

    正因如此，**推荐做法是把 live Key 的交易币种直接设为「全部」**，
    从根上绕开这个坑（2026-09-25 实盘复盘结论：白名单作用于**计价币**，
    不含 USDT 时连 BTC-USDT 都开不了）。
    """
    # ⚠️ `tdMode` 是**必填**：不传会被 OKX 以 `50014 Parameter tdMode can not be empty`
    # 打回，看起来像"权限探测失败"，实则是参数缺失（本脚本第一版即如此）。
    ok, data = _get("/api/v5/account/max-size", environment,
                    {"instId": inst_id, "tdMode": "cross"})
    if not ok:
        text = str(data)
        if "50123" in text:
            return (VERDICT_BLOCK,
                    f"{inst_id} 被拒 50123 —— 该 Key 的交易币种白名单不含它"
                    "（大概率缺**计价币 USDT**）。请把白名单改为「全部」")
        return (VERDICT_WARN, f"{inst_id} 探测未通过（{text[:160]}）—— 请人工确认")
    return (VERDICT_OK,
            f"{inst_id} 只读探测通过（注意：不能替代真实下单时的白名单校验）")


def main() -> int:
    parser = argparse.ArgumentParser(description="ASTRA 实盘切换前只读体检（绝不下一单）")
    parser.add_argument("--env", choices=("demo", "live"), default=None,
                        help="只覆盖本次进程的环境选择，不改任何持久化开关")
    parser.add_argument("--inst", default="BTC-USDT-SWAP",
                        help="用于权限探测的合约（默认 BTC-USDT-SWAP）")
    arguments = parser.parse_args()

    environment = _resolve_environment(arguments.env)
    print("=" * 72)
    print(f"ASTRA 实盘切换前只读体检｜环境 = {environment.mode}"
          f"{'（模拟盘）' if environment.simulated else '（**实盘**）'}")
    print("=" * 72)

    checks: list[tuple[str, str, str]] = []
    verdict, detail = check_credentials(environment)
    checks.append(("凭据完整性", verdict, detail))

    config_verdict, config_detail, _ = check_account_config(environment)
    checks.append(("账户/持仓模式/权限", config_verdict, config_detail))
    # ⚠️ 余额/持仓/权限探测**独立于**账户配置的判定，必须**照跑**：
    # 否则一旦账户模式有问题（BLOCK），操作者就看不到"实盘到底有多少钱、
    # 有没有既有仓"——而这两条正是**定风控上限**与**判断能否干净起步**的依据
    # （本脚本第二版实测踩到：实盘 posMode=net 被 BLOCK 后，余额被整段跳过）。
    checks.append(("账户余额", *check_balance(environment)))
    checks.append(("既有持仓", *check_open_positions(environment)))
    checks.append((f"合约权限探测（{arguments.inst}）",
                   *check_trade_permission_for_instrument(environment, arguments.inst)))

    blocked = False
    warned = False
    for name, verdict, detail in checks:
        icon = {VERDICT_OK: "✅", VERDICT_WARN: "⚠️ ", VERDICT_BLOCK: "❌"}[verdict]
        print(f"\n{icon} {name}｜{verdict}\n    {detail}")
        blocked = blocked or verdict == VERDICT_BLOCK
        warned = warned or verdict == VERDICT_WARN

    print("\n" + "=" * 72)
    if blocked:
        print("结论：存在**阻塞项** —— 现在切 live 会失败或会产生风险，请先修掉上面 ❌ 的项。")
        return 1
    if warned:
        print("结论：无阻塞项，但有告警 —— 请人工确认上面 ⚠️ 的项后再切。")
        return 2
    print("结论：全部通过。可以进入下一阶段（收拾状态文件 → 收窄风控 → 切 live）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
