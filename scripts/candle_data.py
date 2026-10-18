"""Shared filtering for raw OKX candles used by trading and backtests."""
from __future__ import annotations

from typing import Any


def closed_okx_candles(rows: Any) -> list[list[Any]]:
    """Keep exchange-confirmed bars in their original (newest-first) order.

    OKX rows are [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
    A missing/unknown confirm flag is not evidence that a candle has closed.
    Filter before slicing or removing metadata; never just discard row zero,
    since the newest returned candle may already be confirmed.

    过滤规则（与上述注释一致）：
    - ``confirm == "0"`` → 丢弃（交易所明说这根 bar 仍在形成）；
    - ``confirm == "1"`` → 保留（已收盘）；
    - 字段不足 / confirm 缺失 → **保留**（缺失≠未收盘，宁可多保留也不能
      把一根已收盘的 bar 误当作未收盘丢掉——本函数是"防伪造"而非"激进清洗"）。
    """
    if not isinstance(rows, (list, tuple)):
        return []
    kept: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        if len(row) >= 9 and str(row[8]) == "0":
            continue  # 交易所明确标记为未收盘，丢弃
        kept.append(list(row))
    return kept
