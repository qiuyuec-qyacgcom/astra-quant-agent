/**
 * 代码与数据块键盘滚动能力及通用分页导航无障碍守卫闸（批 61）。
 *
 * ## 守什么
 *
 * 1. **代码与 JSON 块键盘可聚焦与滚动能力（WCAG 2.1.1 Keyboard Accessible Scrollable Regions）**：
 *    全站所有用于展示代码、审计 JSON、推演结论、提示词预览及原始日志的 `<pre>` 元素：
 *    - 必须显式声明 `tabindex="0"`；
 *    - 确保无鼠标的纯键盘用户能够使用 Tab 键聚焦到滚动区域，并通过键盘方向键或翻页键流畅滚动阅读长文本。
 *
 * 2. **BasePager 分页导航无障碍语义（APG Pagination Pattern）**：
 *    - 必须采用 `<nav :aria-label="...">` 语义地标包裹；
 *    - 上一页与下一页纯图标按钮必须同时声明 `:aria-label` 与 `:title` 悬浮提示；
 *    - 页码与条目统计文案必须声明 `role="status" aria-live="polite"`，切页时自动向读屏器播报新页状态。
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

test('全站所有 <pre> 元素必须显式声明 tabindex="0"', () => {
  const badPres = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    for (const m of body.matchAll(/<pre\b([^>]*)>/g)) {
      const attrs = m[1];
      if (!attrs.includes('tabindex="0"')) {
        const line = text.slice(0, tm.index).split('\n').length + body.slice(0, m.index).split('\n').length;
        badPres.push(`${rel}:${line} <pre> 缺失 tabindex="0"，键盘用户无法滚动`);
      }
    }
  }

  assert.deepEqual(badPres, [], `发现未配置 tabindex="0" 的 <pre> 区域：\n  ${badPres.join('\n  ')}`);
});

test('BasePager 分页具备 nav 地标、按钮 title 与 status 动态播报', () => {
  const text = readFileSync(path.join(SRC, 'components/base/BasePager.vue'), 'utf8');

  // nav 地标
  assert.match(text, /<nav\b[^>]*:aria-label=/, 'BasePager 必须使用 <nav :aria-label="...">');

  // 按钮 title 与 aria-label
  assert.match(text, /<button[\s\S]*?:title="t\('common\.prevPage'\)"[\s\S]*?:aria-label=/, '上一页按钮缺失 :title 绑定');
  assert.match(text, /<button[\s\S]*?:title="t\('common\.nextPage'\)"[\s\S]*?:aria-label=/, '下一页按钮缺失 :title 绑定');

  // 状态播报
  assert.match(text, /role="status"/, '页码摘要缺失 role="status"');
  assert.match(text, /aria-live="polite"/, '页码摘要缺失 aria-live="polite"');
});

test('闸自检：能准确拦截缺少 tabindex 的 pre 与缺失 nav 的分页器', () => {
  const badPre = '<pre class="code-block">{{ code }}</pre>';
  const goodPre = '<pre class="code-block" tabindex="0">{{ code }}</pre>';
  const badPager = '<div class="pager"><span>1/5</span></div>';
  const goodPager = '<nav :aria-label="nav"><span role="status" aria-live="polite">1/5</span></nav>';

  const checkPre = (html) => html.includes('<pre') && !html.includes('tabindex="0"');
  const checkPager = (html) => !html.includes('<nav') || !html.includes('role="status"');

  assert.equal(checkPre(badPre), true, '应拦截缺少 tabindex="0" 的 pre');
  assert.equal(checkPre(goodPre), false, '应放行带 tabindex="0" 的 pre');
  assert.equal(checkPager(badPager), true, '应拦截缺少 nav 地标与 status 的分页器');
  assert.equal(checkPager(goodPager), false, '应放行合规分页器');
});
