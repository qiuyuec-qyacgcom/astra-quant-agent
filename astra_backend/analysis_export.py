"""Disk-backed analysis bundles; large bodies are decoded and written incrementally."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import csv
import hashlib
import io
import json
import zipfile

from astra_backend.analysis_metrics import VERSION, decimal, summarize
from astra_backend.analysis_store import Archive, now_ms, sanitize, time_text


class BundleWriter:
    def __init__(self, target, secrets):
        # Export latency matters more than the smallest possible download.
        self.zip = zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1)
        self.secrets = set(secrets)
        self.files = {}
        # Prompts repeat across events. Cache only within this export, using its
        # fixed secret snapshot; bound both entry count and retained text size.
        self._cached_text = lru_cache(maxsize=128)(lambda text: sanitize(text, self.secrets))

    def _sanitize_text(self, text):
        if len(text) > 65536:
            return sanitize(text, self.secrets)
        return self._cached_text(text)

    def close(self):
        try:
            self.zip.close()
        finally:
            self._cached_text.cache_clear()

    @contextmanager
    def member(self, name):
        checksum = hashlib.sha256()
        size = 0
        with self.zip.open(name, "w", force_zip64=True) as target:
            def write(text):
                nonlocal size
                data = text.encode("utf-8")
                target.write(data)
                checksum.update(data)
                size += len(data)
            yield write
        self.files[name] = {"sha256": checksum.hexdigest(), "bytes": size}

    def encode(self, body):
        return json.dumps(sanitize(body, self.secrets, _text_sanitizer=self._sanitize_text),
                          ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    def json(self, name, body):
        with self.member(name) as write:
            write(self.encode(body))

    def array(self, write, rows):
        write("[")
        for index, row in enumerate(rows):
            if index:
                write(",")
            write(self.encode(row))
        write("]")


def write_bundle(account, query, target, archive=None, progress=None):
    # Late import keeps the existing public service functions usable by callers.
    from astra_backend import analysis_service as service

    archive = archive or Archive()
    report = progress or (lambda stage, completed=0, total=0: None)
    report("reading")
    with archive.connect() as con:
        if con:
            con.execute("SELECT 1 FROM analysis_events LIMIT 1").fetchone()
        cutoff = now_ms()
        trades = archive.trades(account, query, con) if con else []
        report("reading")
        all_events = archive.events(account, con) if con else []
        report("reading")
        events = service.selected_events(all_events, trades, query)
        report("reading")
        configs = archive.configurations(account, con) if con else []
        configs_by_id = {c["id"]: c for c in configs}
        used = {e["config_id"] for e in events if e["config_id"]} | {
            t["config_id"] for t in trades if t.get("config_id")}
        runtime, latest_events = {}, {}
        for event in all_events:
            cfg = configs_by_id.get(event["config_id"])
            if event["kind"] == "cycle.start" and cfg:
                runtime[cfg["process"]] = {
                    "config_id": cfg["id"], "last_observed_ms": event["observed_ms"], "pid": cfg["pid"]}
            if event["kind"] in ("account.snapshot", "execution.cycle_state"):
                latest_events[event["kind"]] = event
        del all_events
        latest = {kind: {**{k:v for k,v in e.items() if k != "body_hash"},
                        "body": archive.read_blob(con, e["body_hash"])}
                  for kind, e in latest_events.items()}
        used |= {e["config_id"] for e in latest.values() if e["config_id"]}
        used |= {v["config_id"] for v in runtime.values()}
        config_count = sum(c["id"] in used for c in configs)
        stats = summarize(trades)
        health = archive.health(account, con) if con else archive.health(account)
        execution = service.execution_summary(service.window_events(events, query),
            lambda key: archive.read_blob(con, key)) if con else {}
        current = service.configuration_detail(account, "current", archive)
        writer = BundleWriter(target, service.sensitive_values())
        try:
            report("trades", 0, 2 * len(trades))
            writer.json("summary.json", {
                "statistics": stats, "filters": query, "health": health,
                "runtime_observations": runtime, "account": account, "execution": execution})
            with writer.member("trades.json") as write:
                def trade_rows():
                    for index, row in enumerate(trades, 1):
                        yield row
                        if index % 128 == 0:
                            report("trades", index, 2 * len(trades))
                writer.array(write, trade_rows())
            columns = ["id","inst","side","status","open_time","close_time","open_px","close_px","sz","lever",
                       "gross_pnl","fee","funding_fee","other_settlement","net_pnl","known_net_pnl","cost_complete",
                       "cost_basis","exit_reason","exit_evidence","decision_id","config_id","confidence","entry_deviation_bps"]
            with writer.member("trades.csv") as write:
                buf = io.StringIO(newline="")
                csv_writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
                csv_writer.writeheader()
                write("\ufeff" + buf.getvalue())
                for index, row in enumerate(trades, 1):
                    buf.seek(0)
                    buf.truncate(0)
                    values = sanitize({k:row.get(k) for k in columns}, writer.secrets)
                    for key, value in values.items():
                        if isinstance(value,str) and value.startswith(("=","+","-","@")) and decimal(value) is None:
                            values[key] = "'" + value
                    csv_writer.writerow(values)
                    write(buf.getvalue())
                    if index % 128 == 0:
                        report("trades", len(trades) + index, 2 * len(trades))
            with writer.member("configurations.json") as write:
                writer.array(write, (
                    {**{k:v for k,v in c.items() if k != "body_hash"}, "body": archive.read_blob(con, c["body_hash"])}
                    for c in configs if c["id"] in used))
            writer.json("current_configuration.json", current)
            writer.json("latest_runtime_state.json", {
                "observations": latest, "note": "账户最近一次归档观测，非导出时实时交易所查询"})
            report("events", 0, len(events))
            with writer.member("events.jsonl") as write:
                for index, event in enumerate(archive.iter_event_bodies(events, con), 1):
                    write(writer.encode(event) + "\n")
                    if index % 128 == 0:
                        report("events", index, len(events))
            report("events", len(events), len(events))
            matches = service._exchange_fact_selector(trades, events, query, cutoff)
            with writer.member("exchange_facts.json") as write:
                write("{")
                for index, source in enumerate(("positions-history", "orders", "fills", "bills")):
                    report("facts." + source)
                    if index:
                        write(",")
                    write(json.dumps(source) + ":")
                    def selected_rows():
                        for count, row in enumerate(archive.iter_raw_rows(account, source, con), 1):
                            if count % 512 == 0:
                                report("facts." + source, count)
                            if matches(source, row):
                                yield row
                    writer.array(write, selected_rows())
                write("}")
            report("finalizing")
            rate = f'{stats["win_rate"]:.2f}%' if stats["win_rate"] is not None else "不可计算"
            with writer.member("README.md") as write:
                write(
                    "# AstraQuant 交易复盘分析包\n\n"
                    f"账户匿名标识：{account}。时区：Asia/Shanghai。归档截止：{time_text(cutoff)}。\n\n"
                    f"已平仓 {stats['closed_count']} 笔；成本完整 {stats['sample_count']} 笔；成本不完整 {stats['incomplete_count']} 笔。净胜率：{rate}。\n\n"
                    "summary.json 提供口径与覆盖状态；trades.csv/JSON 是全部筛选交易；events.jsonl 按 ID 关联请求、裁决和执行，包含区间外必要证据。\n\n"
                    "configurations.json 是事件发生时采集的历史配置；current_configuration.json 是导出时保存值，不能用于替代历史值。"
                    "runtime_observations 只表示最近观测，不证明进程现在仍运行。\n\n"
                    "净胜率=净盈利笔数/成本完整的已平仓笔数，保本计入分母。利润因子=盈利总额/亏损总额绝对值；"
                    "实际盈亏比=平均盈利/平均亏损绝对值。无亏损或无样本时相应比率为 null。\n\n"
                    "回撤是已实现收益曲线的绝对回撤，不是账户净值回撤。置信度为模型评分。极值来自巡检采样。"
                    "未知费用不按零处理，推断退出原因不当成已确认事实。分组差异不直接证明因果，未成交订单不计算假设盈亏。\n\n"
                    "历史覆盖受交易所保留期与采集起点限制。所有文本为 UTF-8，凭证已脱敏。"
                    "分析时将事实、假设和待验证建议分开，并引用交易及事件 ID。\n")
            writer.json("manifest.json", {
                "format": "astra-analysis-bundle", "version": 1, "statistics_version": VERSION,
                "account": account, "timezone": "Asia/Shanghai", "filters": query,
                "archive_cutoff_ms": cutoff, "current_configuration_captured_ms": current["captured_ms"],
                "counts": {"trades": len(trades), "events": len(events), "configurations": config_count},
                "files": dict(writer.files)})
        finally:
            writer.close()
