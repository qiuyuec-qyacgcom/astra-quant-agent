/**
 * 图标尺寸契约（批 36）。
 *
 * ## 守什么
 *
 * 1. **图标不小于 11px** —— 与字阶地板同级。批 36 实测全站还有 5 处 `:size="10"`
 *    （都在徽标里，紧挨 11.5px 的徽标正文），10px 的线性图标笔画糊成一团。
 * 2. **刷新图标固定 14px** —— 同一个"刷新/重试"动作此前有 12/13/14 三种尺寸
 *    （`SecurityPage` 12、`llm/*` 13、其余 15 个页面 14）。实测 103 个 `btn-sm`
 *    按钮里的图标本就是 14，故以 14 为契约。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function vueFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...vueFiles(p));
    else if (name.endsWith('.vue')) out.push(p);
  }
  return out;
}

const files = vueFiles(SRC).map((f) => ({ rel: path.relative(SRC, f), text: readFileSync(f, 'utf8') }));

/** 收集 `<Comp :size="N" />` 形式的用法；返回 {size, comp, line} */
function sizeUsages(text) {
  const out = [];
  const re = /<([A-Z][A-Za-z0-9]*)\b[^>]*?:size="(\d+)"/g;
  let m;
  while ((m = re.exec(text))) {
    out.push({ comp: m[1], size: Number(m[2]), line: text.slice(0, m.index).split('\n').length });
  }
  return out;
}

test('图标尺寸有下限（≥11px，与字阶地板同级）', () => {
  const bad = [];
  for (const { rel, text } of files) {
    for (const u of sizeUsages(text)) {
      if (u.size < 11) bad.push(`${rel}:${u.line} <${u.comp} :size="${u.size}">`);
    }
  }
  assert.deepEqual(bad, [], `图标小于 11px：\n  ${bad.join('\n  ')}`);
});

test('刷新/重试图标统一 14px', () => {
  const bad = [];
  for (const { rel, text } of files) {
    for (const u of sizeUsages(text)) {
      if (u.comp === 'RefreshCw' && u.size !== 14) bad.push(`${rel}:${u.line} :size="${u.size}"`);
    }
  }
  assert.deepEqual(bad, [], `刷新图标应为 14px：\n  ${bad.join('\n  ')}`);
});

test('防呆自检：规则能真的命中', () => {
  const usages = sizeUsages('<RefreshCw :size="12" /><Foo :size="10" :class="x" />');
  assert.equal(usages.length, 2, '解析器没抓到用法');
  assert.equal(usages[0].comp, 'RefreshCw');
  assert.ok(usages.some((u) => u.size < 11), '下限规则失效');
});
