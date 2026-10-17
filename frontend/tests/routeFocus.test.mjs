/**
 * 切页焦点与「当前项」标记守卫闸（批 112）。
 *
 * ## 实测出来的两处
 *
 * 1. **SPA 切页后焦点没人管**：在 `/admin/overview` 点侧栏跳到 `/admin/security`，
 *    `document.activeElement` 是 **`BODY`** —— 标题对了（各页 title 都正确且唯一），
 *    但对键盘/读屏用户来说切页没有任何信号，下一次 Tab 只能从文档最前面重摸。
 *    修法：`useRouteFocus()` 在 `route.fullPath` 变化后把焦点交给
 *    `<main id="main-content" tabindex="-1">`（两个布局本来就有这个钩子，
 *    跳转链接也指向它）。实测修后：admin 与仪表盘切页焦点都落 `main-content`，
 *    再 Tab 落在主内容内（实测"刷新"）。
 *
 * 2. **文档目录的"当前小节"只靠内联样式**：`DocsView` 的目录按钮用
 *    `:style="activeSection === s.id ? …` 上色，**没有 `aria-current`**，
 *    读屏用户不知道自己在哪一节。修法：加
 *    `:aria-current="activeSection === s.id ? 'location' : undefined"`
 *    （TOC 当前项该用 `location`，不是 `true`）。实测：任意时刻恰好 1 个
 *    `aria-current="location"`，点第 3 项后标记跟着移动。
 *
 * ## 与批 111 是同一主题的两面
 *
 * 「切页后把焦点交给主内容」必须**只在切页时**做：首次进入若就抢焦点，
 * 键盘用户按 Tab 会直接落进主内容，**「跳到主内容」永远用不上**。
 * 所以本闸专门守住 `watch` 不许带 `immediate`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
/** 剥注释：本仓踩过太多次「注释里的例子被当成代码」。 */
const stripComments = (s) =>
  s
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|\s)\/\/[^\n]*/g, ' ');

const read = (rel) => readFileSync(path.join(SRC, rel), 'utf8');
const readCode = (rel) => stripComments(read(rel));

test('切页焦点：watch 不得带 immediate（首次进入不能抢焦点）', () => {
  const src = readCode('composables/useRouteFocus.ts');
  assert.match(src, /watch\(/, 'watch 不见了');
  assert.ok(!/immediate\s*:/.test(src), 'watch 带了 immediate —— 首次进入就会抢焦点，「跳到主内容」会被绕开');
});

test('切页焦点：盯 fullPath（同路径换 query 也算切页）而不是 path', () => {
  const src = readCode('composables/useRouteFocus.ts');
  assert.match(src, /route\.fullPath/, '没有盯 route.fullPath');
  assert.ok(!/route\.path\b/.test(src), '退回 route.path 了 —— 同路径换 query 不会触发');
});

test('两个布局都必须挂上切页焦点，且主内容 id 与组合式默认值一致', () => {
  const layouts = readdirSync(path.join(SRC, 'layouts')).filter((n) => n.endsWith('.vue'));
  assert.ok(layouts.length >= 2, '布局数量不对，判据前提变了');
  for (const l of layouts) {
    const src = readCode(path.join('layouts', l));
    assert.match(src, /useRouteFocus\(\)/, `${l} 没有调用 useRouteFocus()`);
    assert.match(src, /<main[\s\S]{0,120}id="main-content"[\s\S]{0,120}tabindex="-1"/, `${l} 的 <main> 缺 id="main-content" 或 tabindex="-1"（焦点落不进去）`);
    assert.match(src, /outline-none|wb-main/, `${l} 的主内容没处理聚焦外观`);
  }
  assert.match(readCode('composables/useRouteFocus.ts'), /containerId = 'main-content'/, '默认容器 id 与布局不一致');
});

test('文档目录的当前小节必须有 aria-current，且用 location 而不是 true', () => {
  const src = readCode('views/docs/DocsView.vue');
  assert.match(src, /:aria-current="activeSection === s\.id \? 'location' : undefined"/, '目录当前项没标 aria-current="location"');
  assert.ok(!/aria-current="true"/.test(src), 'TOC 当前项该用 location，不是 true');
});

test('判据自检：剥注释后仍能识别真正的 immediate 与 fullPath', () => {
  assert.ok(!/immediate\s*:/.test(stripComments('// 说明：watch 不带 immediate，首次进入不抢')), '注释被当成代码了');
  assert.ok(/immediate\s*:/.test(stripComments('watch(x, f, { immediate: true })')), '真正的 immediate 没被识别');
  assert.ok(/route\.fullPath/.test(stripComments("watch(() => route.fullPath, f)")), 'fullPath 没被识别');
});
