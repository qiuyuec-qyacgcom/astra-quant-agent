"""Shared filtering for raw OKX candles used by trading and backtests."""
from __future__ import annotations

from typing import Any


def closed_okx_candles(rows: Any) -> list[list[Any]]:
    """Keep exchange-confirmed bars in their original (newest-first) order.

    OKX rows are [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
    A missing/unknown confirm flag is not evidence that a candle has closed.
    Filter before slicing or removing metadata; never just discard row zero,
    since the newest returned candle may already be confirmed.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    return [
        list(row)
        for row in rows
        if isinstance(row, (list, tuple)) and len(row) >= 9 and str(row[8]) == "1"
    ]
