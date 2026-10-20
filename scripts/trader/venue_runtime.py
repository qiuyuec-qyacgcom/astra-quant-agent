"""主脑读路径的「场所运行时」薄封装（币安接入执行面）。

背景：主脑 `execute_portfolio` 的持仓/挂单/余额三读原先硬编码走 OKX 直签
（`okx_rest.positions()/pending_orders()/balances()`）。为让「前端切换按钮 →
`preferred_venue`」真正联动执行面，这里把三读收敛成一个薄对象：

- ``get_venue_runtime("okx", ...)`` 直接返回 ``okx_rest`` 模块本身 —— 其
  ``positions()/pending_orders()/balances()`` 签名天然一致，零封装开销。
- ``get_venue_runtime("binance", ...)`` 返回一个薄对象，把币安 adapter 的三读
  统一到与 OKX 相同的调用形状。关键归一：币安 ``account_snapshot()`` 返回
  ``dict``，而 OKX ``balances()`` 返回 ``list[dict]`` —— 这里把币安 dict 包成
  ``[snapshot]``，使下游 ``bal_res[0]["details"]`` 的遍历逻辑无需分所判断。

不变量：``auto``/``okx``/非法值一律按 OKX 处理（默认行为逐位不变）；``binance``
时若 adapter 不可用（未配置/未开闸），三读返回空（``[]``），由下游 fail-closed
逻辑（读到空 → 中止或拒单）兜底，**绝不改派 OKX**。
"""
from __future__ import annotations


class _BinanceReadRuntime:
    """币安读路径薄运行时：把 BinanceTradingAdapter 三读对齐到 OKX 调用形状。"""

    def __init__(self, binance_rest):
        self._binance_rest = binance_rest

    def positions(self, *args, **kwargs):
        if self._binance_rest is None:
            return []
        return self._binance_rest.positions(*args, **kwargs) or []

    def pending_orders(self, *args, **kwargs):
        if self._binance_rest is None:
            return []
        # getattr 守卫：币安 adapter 需桥接 open_orders（能力差异方法），缺失则空。
        _fn = getattr(self._binance_rest, "open_orders", None)
        return (_fn(*args, **kwargs) or []) if _fn else []

    def balances(self, *args, **kwargs):
        if self._binance_rest is None:
            return []
        # 币安 account_snapshot 返回 dict（{"totalEq", "details":[...]}）；
        # OKX balances() 返回 list[dict]。包成 list 对齐下游 `bal_res[0]["details"]`。
        snap = self._binance_rest.account_snapshot()
        return [snap] if snap else []


def get_venue_runtime(venue, *, okx_rest, binance_rest=None):
    """按场所返回主脑读路径运行时。

    - ``venue == "binance"`` → 币安薄对象（三读统一形状，adapter 缺失时返回空）。
    - 其余（``auto``/``okx``/非法）→ 直接返回 ``okx_rest`` 模块（零封装）。
    """
    if str(venue or "okx").lower() == "binance":
        return _BinanceReadRuntime(binance_rest)
    return okx_rest
