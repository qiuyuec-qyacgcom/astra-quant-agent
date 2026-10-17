/**
 * 表格行嵌套契约（批 34）。
 *
 * ## 守什么
 *
 * `DataTable` 的行外壳本身就是 `<tr>`，`#row` 槽是**塞进这个 `<tr>` 里的**
 * 内容。所以槽里只能写 `<td>`；再套一层 `<tr>` 会得到 `<tr><tr>…</tr></tr>`：
 *
 * - **HTML 非法**（`<tr>` 不能是 `<tr>` 的子元素），
 * - DOM 里行数直接翻倍（实测 `/admin/gateway` 投递表 50 条数据渲染出 100 个 `<tr>`），
 * - 外层空 `<tr>` 套着内层真行，行高实测 34/35/36 三档混杂，
 * - `:hover`、`border-collapse`、读屏器的行语义全部错位。
 *
 * 需要自定义行样式时用 `DataTable` 的 `row-class` 属性（批 34 新增），
 * 不要自己套 `<tr>`。
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

const files = vueFiles(SRC);

/**
 * 注意两条规则的覆盖差异（已做变异验证）：
 *
 * - 规则一（模板文本里 `<tr>` 紧跟 `<tr>`）**抓不到**当时的真实 bug ——
 *   源码里两层 `<tr>` 之间隔着 `<template #row>` 标签，只有运行时才嵌套；
 *   它抓的是静态写死的兄弟相邻情况，属兜底。
 * - 规则二（`#row` 槽以 `<tr>` 开头）**能抓到**：拿 `git show HEAD:…GatewayPage.vue`
 *   验证过，命中第 277 行。真正的守卫是这条。
 */
test('模板里不存在 <tr> 直接嵌 <tr>', () => {
  const bad = [];
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    // <tr ...> 后面（允许属性/空白/注释）紧跟另一个 <tr
    // 批 43：注释匹配收紧成 `<!--(?:(?!-->)[\s\S])*-->`。原来的 `[\s\S]*?--` 允许
    // 注释体里出现 `-->`，于是**跨注释回溯**：源码里插一条新注释后，正则会从上一个
    // `<tr ...>` 一路吞到后面某条注释的 `-->`，把中间的表头、按钮全当注释，
    // 命中本文件里相隔四十行的两个 `<tr>`（批 43 实测误报 DataTable:165）。
    const re = /<tr\b[^>]*>\s*(?:<!--(?:(?!-->)[\s\S])*-->\s*)*<tr\b/g;
    let m;
    while ((m = re.exec(text))) {
      const line = text.slice(0, m.index).split('\n').length;
      bad.push(`${path.relative(SRC, f)}:${line}`);
    }
  }
  assert.deepEqual(bad, [], `行里不能再套行（会渲染成 <tr><tr>，行数翻倍且行高不稳）：\n  ${bad.join('\n  ')}`);
});

test('DataTable 的 #row 槽内容不以 <tr> 开头（应只写 <td>，样式走 row-class）', () => {
  const bad = [];
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    const re = /<template\s+#row\b[^>]*>/g;
    let m;
    while ((m = re.exec(text))) {
      const rest = text.slice(m.index + m[0].length);
      if (/^\s*(?:<!--[\s\S]*?-->\s*)*<tr\b/.test(rest)) {
        const line = text.slice(0, m.index).split('\n').length;
        bad.push(`${path.relative(SRC, f)}:${line}`);
      }
    }
  }
  assert.deepEqual(bad, [], `#row 槽里套了 <tr>：外壳已经是一行，请把类交给 DataTable 的 row-class 属性：\n  ${bad.join('\n  ')}`);
});

test('防呆自检：上面两条规则能真的命中（拿历史写法当样本）', () => {
  const nested = '<template #row="{ row: d }">\n  <tr class="gw-tr">\n    <td>x</td>\n  </tr>\n</template>';
  assert.ok(/<tr\b[^>]*>\s*<tr\b/.test('<tr class="a"><tr class="b">'), '规则一失效');
  const re = /<template\s+#row\b[^>]*>/g;
  const m = re.exec(nested);
  assert.ok(m && /^\s*(?:<!--[\s\S]*?-->\s*)*<tr\b/.test(nested.slice(m.index + m[0].length)), '规则二失效');
});
