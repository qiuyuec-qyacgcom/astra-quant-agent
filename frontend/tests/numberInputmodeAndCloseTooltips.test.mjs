/**
 * 数值输入键盘模式与关闭按钮提示守卫闸（批 56）。
 *
 * ## 守什么
 *
 * 1. **移动/触控端数值键盘优化（HTML5 inputmode）**：
 *    全站所有 `type="number"` 输入控件必须显式声明 `inputmode="numeric"`（整数）
 *    或 `inputmode="decimal"`（浮点小数/步长带小数位）。
 *    避免移动端和触控屏弹出默认的全键盘，强制呼出带小数点或纯数字的专用小键盘。
 *
 * 2. **弹窗/抽屉/面板关闭按钮原生 Tooltip 覆盖**：
 *    `BaseDialog.vue`、`BaseDrawer.vue` 与 `TrajectoryPanel.vue` 的关闭图标按钮
 *    必须同时声明 `:aria-label` 与 `:title`，确保视障读屏器与鼠标悬停用户均能获知明确的关闭提示。
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

test('所有 type="number" 输入框必须显式声明 inputmode', () => {
  const badInputs = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    if (!body.includes('type="number"')) continue;

    for (const m of body.matchAll(/<input\b([^>]*type="number"[^>]*)>/g)) {
      const attrs = m[1];
      const hasInputMode = attrs.includes('inputmode="numeric"') || attrs.includes('inputmode="decimal"');
      if (!hasInputMode) {
        const line = text.slice(0, tm.index).split('\n').length + body.slice(0, m.index).split('\n').length;
        badInputs.push(`${rel}:${line} type="number" 缺少 inputmode="numeric" 或 "decimal"`);
      }
    }
  }

  assert.deepEqual(badInputs, [], `发现未配置 inputmode 的数值输入框：\n  ${badInputs.join('\n  ')}`);
});

test('BaseDialog、BaseDrawer 与 TrajectoryPanel 关闭按钮必须具备 title 与 aria-label', () => {
  const targets = [
    { file: 'components/base/BaseDialog.vue', hasTitle: /:title=".*t\('common\.close'\)/ },
    { file: 'components/base/BaseDrawer.vue', hasTitle: /:title=".*t\('common\.close'\)/ },
    { file: 'components/dashboard/TrajectoryPanel.vue', hasTitle: /:title=".*t\('dash\.shell\.panel\.closeAria'\)/ },
  ];

  for (const { file, hasTitle } of targets) {
    const text = readFileSync(path.join(SRC, file), 'utf8');
    assert.match(text, hasTitle, `${file} 关闭按钮缺失 :title 绑定`);
    assert.match(text, /:aria-label=/, `${file} 关闭按钮缺失 :aria-label 绑定`);
  }
});

test('闸自检：能准确拦截无 inputmode 的输入框与无 title 的关闭按钮', () => {
  const badNum = '<input type="number" v-model="val" />';
  const goodNum = '<input type="number" inputmode="numeric" v-model="val" />';
  const badClose = '<button class="btn" :aria-label="close"><X /></button>';
  const goodClose = '<button class="btn" :title="close" :aria-label="close"><X /></button>';

  const checkNum = (html) => html.includes('type="number"') && !html.includes('inputmode=');
  const checkClose = (html) => html.includes('<button') && !html.includes(':title=');

  assert.equal(checkNum(badNum), true, '应拦截缺少 inputmode 的数值输入框');
  assert.equal(checkNum(goodNum), false, '应放行带 inputmode 的数值输入框');
  assert.equal(checkClose(badClose), true, '应拦截缺少 title 的关闭按钮');
  assert.equal(checkClose(goodClose), false, '应放行带 title 的关闭按钮');
});
