/**
 * 「网关调度表字段契约」守卫闸（2026-10-22）。
 *
 * ## 事故背景
 *
 * 「运行单元 → 调度与投递」页的调度作业表有两列**从项目诞生起就是坏的**：
 *
 * | 列 | 前端读的字段 | 后端 `scheduler_snapshot()` 实际返回 | 结果 |
 * |---|---|---|---|
 * | 触发 | `j.interval_seconds` | `schedule`（人类可读文本） | 永远 `undefined` → 空列 |
 * | 最近执行时间 | `j.last_run` | `last_scheduled_at` | 永远 `undefined` → 永远「尚未调度」 |
 *
 * 后端契约由 `tests/ops/test_gateway_scheduler.py` 钉住：`scheduler_snapshot()`
 * 返回 `last_scheduled_at` / `schedule` / `offset_seconds` / `overdue`，
 * **没有** `last_run` 也没有 `interval_seconds`。前端却各说各话地读了两个不存在的
 * 字段 —— 于是即使定时调度真实触发了、`runtime_state` 也写了 `job.last.*`，
 * 页面依旧显示「尚未调度」。
 *
 * 本闸锁住两件事：
 * 1. 前端**必须**读后端真实返回的 `last_scheduled_at` 与 `schedule`；
 * 2. 前端**不得**再读不存在的 `j.last_run` 与 `j.interval_seconds`
 *    （那是静默坏列的根源 —— 不报错、测试也不红，只能靠静态判据按住）。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const PAGE = path.join(SRC, 'views/admin/GatewayPage.vue');
const read = () => readFileSync(PAGE, 'utf8');

test('调度表「最近执行时间」必须读后端契约字段 last_scheduled_at', () => {
  const src = read();
  assert.ok(
    src.includes('j.last_scheduled_at'),
    'GatewayPage 应读 j.last_scheduled_at（后端 scheduler_snapshot 的真实字段）',
  );
});

test('调度表「触发」列必须读后端契约字段 schedule', () => {
  const src = read();
  assert.ok(
    src.includes('j.schedule'),
    'GatewayPage 应读 j.schedule（后端已生成的人类可读触发描述）',
  );
});

test('调度表不得再读不存在的 j.last_run（静默坏列根源）', () => {
  const src = read();
  assert.ok(
    !/\bj\.last_run\b/.test(src),
    'j.last_run 不是后端契约字段，读了会永远显示「尚未调度」',
  );
});

test('调度表不得再读不存在的 j.interval_seconds（静默坏列根源）', () => {
  const src = read();
  assert.ok(
    !/\bj\.interval_seconds\b/.test(src),
    'j.interval_seconds 不是后端契约字段，读了会永远空列',
  );
});
