/**
 * R:R 试算台必须跟随风控页配置的 `ASTRA_MIN_RISK_REWARD`，而不是写死 2.0。
 *
 * 后端 `/api/all → portfolio_risk.min_rr` 是单一事实源；本文件只钉纯函数
 * `computeRiskReward` 的判定口径：
 * - `minRr` 缺省仍是 2.0（兼容旧缓存）；
 * - 传入 1.8 时 1.8 的报价应放行，2.0 的报价在 2.5 底线前应拒绝；
 * - 展示为 2.00 的比值不得因二进制误差被判不足。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { computeRiskReward } from '../src/components/dashboard/chartMath.ts';

const base = {
  entry: 100,
  sl: 90,
  tp: 120,
  side: 'long',
  atr: 5,
  activePosition: null,
  availEq: 1000,
};

test('试算台按风控页配置的 R:R 底线判定', () => {
  assert.equal(computeRiskReward({ ...base, minRr: 2.0 }).isRrCompliant, true);
  assert.equal(computeRiskReward({ ...base, minRr: 2.5 }).isRrCompliant, false);
  assert.equal(computeRiskReward({ ...base, minRr: 1.8 }).isRrCompliant, true);
  assert.equal(computeRiskReward(base).minRr, 2.0, '缺省仍按 2.0 兼容旧缓存');
});

test('显示 2.00 的边界不因浮点误差被判不足', () => {
  const result = computeRiskReward({
    entry: 1.1,
    sl: 0.9,
    tp: 1.5,
    side: 'long',
    atr: 0.1,
    activePosition: null,
    availEq: 1000,
    minRr: 2,
  });
  assert.equal(result.rrRatio.toFixed(2), '2.00');
  assert.equal(result.isRrCompliant, true);
});
