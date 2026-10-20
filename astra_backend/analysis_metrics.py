"""Shared position-lifecycle statistics. No network or filesystem side effects."""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import math
from typing import Any

VERSION = "position-net-v1"

def decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def number(value: Any) -> float | None:
    result = decimal(value)
    return float(result) if result is not None else None

def cost_complete(t: dict) -> bool:
    return t.get("cost_complete") is True and decimal(t.get("net_pnl")) is not None

def duration_minutes(t: dict) -> float | None:
    start, end = number(t.get("open_ms")), number(t.get("close_ms"))
    return max(0, (end - start) / 60000) if start is not None and end is not None else None

def summarize(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("status") == "closed"]
    valid = sorted((t for t in closed if cost_complete(t)), key=lambda t: (t.get("close_ms") or 0, t.get("id", "")))
    values = [decimal(t["net_pnl"]) for t in valid]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    n, nw, nl = len(values), len(wins), len(losses)
    win_sum, loss_sum = sum(wins, Decimal(0)), -sum(losses, Decimal(0))
    avg_win = win_sum / nw if nw else None
    avg_loss = loss_sum / nl if nl else None
    pf = win_sum / loss_sum if loss_sum else None
    p = nw / n if n else None
    interval = None
    if n:
        z = 1.959963984540054
        center = (p + z*z/(2*n)) / (1+z*z/n)
        spread = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        interval = [100*(center-spread), 100*(center+spread)]
    cumulative = peak = max_dd = Decimal(0)
    streak = longest = 0
    curve = []
    for t, value in zip(valid, values):
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_dd = max(max_dd, drawdown)
        streak = streak + 1 if value < 0 else 0
        longest = max(longest, streak)
        curve.append({"time_ms": t.get("close_ms"), "trade_id": t.get("id"), "net": float(cumulative), "drawdown": float(drawdown)})
    def summed(key):
        vals = [decimal(t.get(key)) for t in valid]
        return float(sum(vals, Decimal(0))) if vals and all(v is not None for v in vals) else None
    return {
        "statistics_version": VERSION, "closed_count": len(closed), "sample_count": n,
        "incomplete_count": len(closed)-n, "holding_count": sum(t.get("status") != "closed" for t in trades),
        "wins": nw, "losses": nl, "breakeven": n-nw-nl,
        "win_rate": 100*p if p is not None else None, "win_rate_ci95": interval,
        "net_pnl": float(cumulative) if n else None,
        "gross_pnl": summed("gross_pnl"), "fee": summed("fee"), "funding_fee": summed("funding_fee"),
        "other_settlement": summed("other_settlement"),
        "total_win_amt": float(win_sum), "total_loss_amt": float(loss_sum),
        "avg_win": float(avg_win) if avg_win is not None else None,
        "avg_loss": float(avg_loss) if avg_loss is not None else None,
        "payoff_ratio": float(avg_win/avg_loss) if avg_win is not None and avg_loss else None,
        "profit_factor": float(pf) if pf is not None else None,
        "profit_factor_status": "defined" if loss_sum else ("no_losses" if win_sum else "no_outcomes"),
        "expectancy": float(cumulative/n) if n else None, "longest_loss_streak": longest,
        "max_realized_drawdown": float(max_dd) if n else None, "curve": curve,
        "decision_link_rate": 100*sum(bool(t.get("decision_id")) for t in closed)/len(closed) if closed else None,
        "cost_complete_rate": 100*n/len(closed) if closed else None,
    }

GROUPS = {"inst", "side", "config_id", "confidence", "duration", "exit_reason"}

def group_key(t: dict, by: str) -> str:
    if by == "confidence":
        v = number(t.get("confidence"))
        if v is None: return "unknown"
        return "<60" if v < 60 else "60–74" if v < 75 else "75–84" if v < 85 else "85–94" if v < 95 else "95–100"
    if by == "duration":
        v = duration_minutes(t)
        if v is None: return "unknown"
        return "<1h" if v < 60 else "1–4h" if v < 240 else "4–12h" if v < 720 else "12–24h" if v < 1440 else "≥24h"
    if by == "exit_reason":
        return f"{t.get('exit_evidence', 'unknown')}: {t.get('exit_reason') or 'unknown'}"
    return str(t.get(by) or "unknown")

def breakdown(trades: list[dict], by: str) -> list[dict]:
    if by not in GROUPS: raise ValueError("不支持的分组维度")
    groups = defaultdict(list)
    for trade in trades:
        if trade.get("status") == "closed": groups[group_key(trade, by)].append(trade)
    return [{"key": key, **{k:v for k,v in summarize(items).items() if k != "curve"}} for key,items in sorted(groups.items())]

def legacy_performance(trades: list[dict]) -> dict:
    stats = summarize(trades)
    return {
        "all_trades": stats["sample_count"], "win_trades": stats["wins"], "loss_trades": stats["losses"],
        "breakeven_trades": stats["breakeven"], "win_rate": stats["win_rate"],
        "profit_factor": stats["profit_factor"], "avg_win": stats["avg_win"], "avg_loss": stats["avg_loss"],
        "total_win_amt": stats["total_win_amt"], "total_loss_amt": stats["total_loss_amt"],
        "incomplete_count": stats["incomplete_count"], "statistics_version": VERSION,
        "leaderboard": sorted([{"inst": g["key"], "trades": g["sample_count"], "wins": g["wins"],
            "losses": g["losses"], "pnl": g["net_pnl"], "win_rate": g["win_rate"]} for g in breakdown(trades,"inst")],
            key=lambda g: g["pnl"] if g["pnl"] is not None else -math.inf, reverse=True),
    }
