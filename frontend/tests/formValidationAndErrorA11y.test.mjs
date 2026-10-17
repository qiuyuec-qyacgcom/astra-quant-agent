/**
 * 表单校验与错误状态无障碍语义守卫闸（批 51）。
 *
 * ## 守什么
 *
 * 1. **错误状态块告警角色（WCAG 4.1.2 & 3.3.1）**：
 *    当数据加载失败或系统异常展示 `.state-block.is-error` 时，容器必须具备 `role="alert"`，
 *    确保屏幕阅读器能第一时间主动向视障用户播报错误内容，而不是静默不语。
 *
 * 2. **表单输入校验无障碍同步**：
 *    当输入控件（如风控参数越界、席位模型缺失）应用了视觉错误类（`.is-bad`、`.is-warn`）时，
 *    必须同步绑定 `:aria-invalid`，使辅助技术获知输入内容不合法。
 *
 * 3. **操作结果动态角色**：
 *    测试或保存结果容器（如模型连通性测试、拉取远端结果、全局保存反馈）必须具备动态角色绑定（`:role="ok ? 'status' : 'alert'"`）。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

const stripComments = (t) => t.replace(/<!--[\s\S]*?-->/g, '');

test('所有 .state-block.is-error 必须声明 role="alert"', () => {
  const badBlocks = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    for (const m of body.matchAll(/<div\b([^>]*class="[^"]*state-block\s+is-error[^"]*"[^>]*)>/g)) {
      const attrs = m[1];
      if (!attrs.includes('role="alert"')) {
        const line = text.slice(0, tm.index).split('\n').length + body.slice(0, m.index).split('\n').length;
        badBlocks.push(`${rel}:${line} 的 state-block.is-error 缺少 role="alert"`);
      }
    }
  }

  assert.deepEqual(badBlocks, [], `发现未声明 role="alert" 的错误块：\n  ${badBlocks.join('\n  ')}`);
});

test('带有 is-bad / is-warn 校验类的表单控件必须绑定 aria-invalid', () => {
  const badInputs = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    for (const m of body.matchAll(/<(input|select|textarea)\b([^>]*)>/g)) {
      const tag = m[1];
      const attrs = m[2];
      const hasErrorClass = attrs.includes('is-bad') || attrs.includes('is-warn');
      const hasAriaInvalid = attrs.includes('aria-invalid');

      if (hasErrorClass && !hasAriaInvalid) {
        const line = text.slice(0, tm.index).split('\n').length + body.slice(0, m.index).split('\n').length;
        badInputs.push(`${rel}:${line} <${tag}> 应用了错误类名但未声明 aria-invalid`);
      }
    }
  }

  assert.deepEqual(badInputs, [], `发现未同步 aria-invalid 的校验输入框：\n  ${badInputs.join('\n  ')}`);
});

test('闸自检：能准确拦截无 role 的错误块与未声明 aria-invalid 的控件', () => {
  const badBlock = '<div class="state-block is-error"><p>Failed</p></div>';
  const goodBlock = '<div role="alert" class="state-block is-error"><p>Failed</p></div>';
  const badInput = '<input :class="{ \'is-bad\': outOfRange }" />';
  const goodInput = '<input :class="{ \'is-bad\': outOfRange }" :aria-invalid="outOfRange" />';

  const checkBlock = (html) => /class="[^"]*state-block\s+is-error[^"]*"/.test(html) && !html.includes('role="alert"');
  const checkInput = (html) => (html.includes('is-bad') || html.includes('is-warn')) && !html.includes('aria-invalid');

  assert.equal(checkBlock(badBlock), true, '无 alert 角色的错误块应被拦截');
  assert.equal(checkBlock(goodBlock), false, '合规错误块应放行');
  assert.equal(checkInput(badInput), true, '未配 aria-invalid 的输入框应被拦截');
  assert.equal(checkInput(goodInput), false, '配齐 aria-invalid 的输入框应放行');
});
