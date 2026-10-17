import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolveEvolutionStatus } from '../src/utils/evolutionStatus.ts';

test('真实自进化状态 ADD / REVISE / INVALIDATE / EVOLVED 必须正确映射为 EVOLVED 而非 FAILED', () => {
  for (const st of ['ADD', 'add', 'REVISE', 'revise', 'INVALIDATE', 'invalidate', 'EVOLVED', 'CHANGED', 'UPDATED']) {
    const res = resolveEvolutionStatus(st);
    assert.equal(res.category, 'EVOLVED', `${st} 必须归类为 EVOLVED`);
    assert.match(res.hudClass, /--up/, `${st} 必须具备绿色 --up 状态类`);
    assert.match(res.hudTextClass, /--up/, `${st} 必须具备绿色 --up 文本类`);
    assert.equal(res.category === 'FAILED', false, `${st} 绝对不得被误判为 FAILED 复盘失败`);
  }
});

test('管理后台徽章样式针对正向进化返回 badge-up，针对淘汰返回 badge-warn', () => {
  assert.equal(resolveEvolutionStatus('ADD').adminBadgeClass, 'badge-up');
  assert.equal(resolveEvolutionStatus('REVISE').adminBadgeClass, 'badge-up');
  assert.equal(resolveEvolutionStatus('EVOLVED').adminBadgeClass, 'badge-up');
  assert.equal(resolveEvolutionStatus('CHANGED').adminBadgeClass, 'badge-up');
  assert.equal(resolveEvolutionStatus('INVALIDATE').adminBadgeClass, 'badge-warn');
});

test('维持现状 NO_CHANGE 与运行中 RUNNING 正确解析', () => {
  const held = resolveEvolutionStatus('NO_CHANGE');
  assert.equal(held.category, 'NO_CHANGE');
  assert.match(held.hudClass, /--ink-2/);
  assert.equal(held.adminBadgeClass, 'badge-info');

  const running = resolveEvolutionStatus('RUNNING');
  assert.equal(running.category, 'RUNNING');
  assert.match(running.hudClass, /--warn/);
  assert.equal(running.adminBadgeClass, 'badge-warn');
});

test('FAILED 状态或存在 llm_error 时必须明确断言为 FAILED', () => {
  const failed = resolveEvolutionStatus('FAILED');
  assert.equal(failed.category, 'FAILED');
  assert.match(failed.hudClass, /--down/);
  assert.equal(failed.adminBadgeClass, 'badge-down');

  const withError = resolveEvolutionStatus('ADD', 'HTTP 504 Gateway Timeout');
  assert.equal(withError.category, 'FAILED', '存在 llm_error 时即使状态为 ADD 也必须呈现失败态');
  assert.match(withError.hudClass, /--down/);
  assert.equal(withError.adminBadgeClass, 'badge-down');
});

test('未知状态优雅降级为 UNKNOWN，绝不一刀切误杀为 FAILED', () => {
  const unknown = resolveEvolutionStatus('SPECIAL_PASS');
  assert.equal(unknown.category, 'UNKNOWN');
  assert.equal(unknown.key, 'SPECIAL_PASS');
  assert.equal(unknown.category === 'FAILED', false, '未知合法状态绝不得误判为失败');
});

test('空值与假值安全回退到 EMPTY', () => {
  for (const empty of [null, undefined, '', '   ']) {
    const res = resolveEvolutionStatus(empty);
    assert.equal(res.category, 'EMPTY');
    assert.equal(res.key, '');
  }
});
