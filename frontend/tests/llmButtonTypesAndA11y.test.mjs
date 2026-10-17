/**
 * LLM 渠道管理子系统按钮类型与表单提交隔离守卫闸（批 119）。
 *
 * ## 背景与规则
 *
 * 在复杂表单视图与弹窗中（如大模型配置页、模型编辑弹窗、远端模型探测弹窗）：
 * HTML 规范规定 `<button>` 缺省 `type` 时默认为 `type="submit"`。
 * 当这些页面内存在 `<form>`（如 ModelEditDialog 内的表单）或未来扩展包裹时，
 * 缺少 `type="button"` 的常规按钮（如回退、参数配置、探测、测试、删除等）
 * 会在用户回车或点击时意外触发表单提交。
 *
 * 本闸确保 `src/views/admin/llm/` 下所有 Vue 模板内的每一个 `<button>`
 * 都显式声明了 `type="button"` 或 `type="submit"`，杜绝默认隐式提交行为。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

const LLM_DIR = path.resolve(import.meta.dirname, '..', 'src/views/admin/llm');

/** 剥除注释，避免注释示例干扰 */
const stripComments = (s) =>
  s
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|\s)\/\/[^\n]*/g, ' ');

export function findUntypedButtons(source) {
  const code = stripComments(source);
  const bad = [];
  const btnRegex = /<button\b([^>]*?)>/gs;
  let m;
  while ((m = btnRegex.exec(code)) !== null) {
    const attrs = m[1];
    if (!/\btype=["'][^"']+["']/.test(attrs)) {
      bad.push(attrs.replace(/\s+/g, ' ').trim().slice(0, 80));
    }
  }
  return bad;
}

test('LLM 模块内所有按钮必须显式声明 type 属性（type="button" 或 type="submit"）', () => {
  const files = readdirSync(LLM_DIR).filter((f) => f.endsWith('.vue'));
  const violations = [];

  for (const f of files) {
    const fullPath = path.join(LLM_DIR, f);
    const content = readFileSync(fullPath, 'utf8');
    const hits = findUntypedButtons(content);
    if (hits.length) {
      violations.push(`${f}: ${hits.length} 处缺少 type [${hits.join('; ')}]`);
    }
  }

  assert.deepEqual(
    violations,
    [],
    'LLM 视图内发现未显式指定 type 的按钮（会默认为 submit 造成误提交）：\n  ' + violations.join('\n  '),
  );
});

test('判据自检：能准确捕获缺失 type 的 button，正常 button 不误报', () => {
  const bad = '<button class="btn btn-ghost" @click="goBack">返回</button>';
  assert.equal(findUntypedButtons(bad).length, 1);

  const okButton = '<button type="button" class="btn btn-ghost" @click="goBack">返回</button>';
  assert.equal(findUntypedButtons(okButton).length, 0);

  const okSubmit = '<button type="submit" form="edit-form">保存</button>';
  assert.equal(findUntypedButtons(okSubmit).length, 0);

  const multiline = '<button\n  type="button"\n  class="btn"\n>多行</button>';
  assert.equal(findUntypedButtons(multiline).length, 0);
});
