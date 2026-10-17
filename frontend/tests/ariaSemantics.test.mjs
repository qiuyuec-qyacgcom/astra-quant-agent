/**
 * 表格与导航的 ARIA 语义闸（批 44）。
 *
 * ## 守什么（四条，都是"视觉上没问题、读屏器上不成立"的细节）
 *
 * 1. **`<th scope>`** —— 不给 `scope` 时，读屏器只能靠位置猜表头与单元格的对应关系；
 *    列头多、有合并单元格时就会串行。显式 `scope="col"` 是规范推荐的写法。
 * 2. **`<table>` 可访问名** —— 表格必须能自报"我是什么表"。周围的小标题不算关联
 *    （它在表格外，不构成 accessible name），所以要么 `aria-label`，要么 `<caption>`。
 * 3. **当前页 `aria-current="page"`** —— 侧边栏高亮此前只是颜色，读屏器用户
 *    完全不知道自己当前在哪个页面（`is-active` / 加粗 / 小圆点都是纯视觉）。
 * 4. **`<img>` 必须有 `alt`** —— 缺 `alt` 时部分读屏器会念出文件名。
 *
 * 判据都是"属性存在性"，不检查取值语义（那要靠人看），故闸本身不会误报成灾。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
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

const stripComments = (text) =>
  text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');

/** 只保留 <template> 段（说明文档里也会出现 <th> 之类的字样） */
function templates() {
  const out = [];
  for (const f of vueFiles(SRC)) {
    const text = stripComments(readFileSync(f, 'utf8'));
    const m = text.match(/<template>([\s\S]*)<\/template>/);
    if (m) out.push([path.relative(SRC, f), m[1]]);
  }
  return out;
}

const TPLS = templates();

test('每个 <th> 都声明 scope', () => {
  const bad = [];
  for (const [rel, tpl] of TPLS) {
    for (const m of tpl.matchAll(/<th(?=[\s>])((?:[^>"]|"[^"]*")*)>/g)) {
      if (!/\bscope=/.test(m[1])) bad.push(`${rel}: <th${m[1].slice(0, 40)}> 无 scope`);
    }
  }
  assert.deepEqual(bad, [], `表头缺 scope（读屏器只能靠位置猜列对应）：\n  ${bad.join('\n  ')}`);
});

test('每个 <table> 都有可访问名（aria-label 或 caption）', () => {
  const bad = [];
  for (const [rel, tpl] of TPLS) {
    // 去掉 <caption>…</caption>，余下部分里的 <table> 必须自带 aria-label
    const captions = [...tpl.matchAll(/<caption[\s\S]*?<\/caption>/g)].map((m) => m.index);
    for (const m of tpl.matchAll(/<table(?=[\s>])((?:[^>"]|"[^"]*")*)>/g)) {
      const hasLabel = /aria-label|aria-labelledby/.test(m[1]);
      const hasCaption = captions.some((i) => i > m.index && i - m.index < 400);
      if (!hasLabel && !hasCaption) bad.push(`${rel}: <table${m[1].slice(0, 40)}> 无名`);
    }
  }
  assert.deepEqual(bad, [], `表格缺可访问名（读屏器不会说"这是什么表"）：\n  ${bad.join('\n  ')}`);
});

test('导航项高亮必须同时给出 aria-current="page"', () => {
  const bad = [];
  for (const [rel, tpl] of TPLS) {
    // 侧边栏导航：出现 is-active 或 activeTab === … 的按钮必须带 aria-current
    for (const m of tpl.matchAll(/<(button|a|RouterLink)\b((?:[^>"]|"[^"]*")*)>/g)) {
      const attrs = m[2];
      const looksActive = /is-active|activeTab === |currentKey === |seg-on/.test(attrs);
      // 三种语义出口都算数：页内导航 aria-current、页签 aria-selected、开关 aria-pressed
      const semantic = /aria-current|aria-selected|aria-pressed/.test(attrs);
      if (looksActive && !semantic) {
        bad.push(`${rel}: <${m[1]} …> 有 active 判定但无 aria-current/aria-selected/aria-pressed`);
      }
    }
  }
  assert.deepEqual(bad, [], `导航高亮只有视觉、没有语义：\n  ${bad.join('\n  ')}`);
});

test('每个 <img> 都有 alt（可为空串表示装饰图）', () => {
  const bad = [];
  for (const [rel, tpl] of TPLS) {
    for (const m of tpl.matchAll(/<img(?=[\s>])((?:[^>"]|"[^"]*")*)>/g)) {
      if (!/\balt=/.test(m[1])) bad.push(`${rel}: <img${m[1].slice(0, 40)}> 无 alt`);
    }
  }
  assert.deepEqual(bad, [], `图片缺 alt（部分读屏器会念文件名）：\n  ${bad.join('\n  ')}`);
});

test('闸自检：四条判据能真的命中', () => {
  const th = (a) => !/\bscope=/.test(a);
  const tbl = (a) => !/aria-label|aria-labelledby/.test(a);
  const nav = (a) =>
    /is-active|seg-on/.test(a) && !/aria-current|aria-selected|aria-pressed/.test(a);
  const img = (a) => !/\balt=/.test(a);
  assert.equal(th(' class="x"'), true);
  assert.equal(th(' scope="col"'), false);
  assert.equal(tbl(' class="table"'), true);
  assert.equal(tbl(' :aria-label="t(\'x\')"'), false);
  assert.equal(nav(' :class="{ \'is-active\': on }"'), true);
  assert.equal(nav(' :class="{ \'is-active\': on }" :aria-current="on ? \'page\' : undefined"'), false);
  assert.equal(nav(' :class="{ \'seg-on\': on }" :aria-selected="on"'), false);
  assert.equal(img(' src="/a.png"'), true);
  assert.equal(img(' src="/a.png" alt=""'), false);
});
