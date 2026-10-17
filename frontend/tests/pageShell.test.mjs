/**
 * 页面骨架闸（批 45）：兜底路由 / 跳转链接 / 标题地标。
 *
 * ## 守什么（都是实测出来的洞）
 *
 * 1. **必须有 catch-all 路由**。此前路由表没有兜底，任何拼错的地址渲染出**一整页空白**：
 *    实测 `/admin/nope-does-not-exist` → `body.innerText` 长度 **0**、零个标题、零个出口。
 * 2. **跳转链接（skip link）**必须挂在两套外壳上，且 `@click.prevent` 自己接管焦点 ——
 *    外壳跑在 `createWebHistory` 下，裸锚点会被 vue-router 当成路由跳转，
 *    在 `/factors` 点一下直接回首页（比没有更糟）。
 * 3. **`<main id="main-content" tabindex="-1">`**：跳转链接的落点必须能接住焦点，
 *    否则焦点留在链接上，读屏器不会开始念正文。
 * 4. 404 视图自己要有 `<h1>`（它不套任何布局，没有布局代它出标题）。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const read = (rel) => readFileSync(path.join(SRC, rel), 'utf8');

const ROUTER = 'router/index.ts';
const LAYOUTS = ['layouts/AdminLayout.vue', 'layouts/DashboardLayout.vue'];

test('路由表必须有 catch-all 兜底（否则拼错地址是一片空白）', () => {
  const text = read(ROUTER);
  assert.match(text, /pathMatch/, '缺少 /:pathMatch(.*)* 兜底路由');
  assert.match(text, /NotFoundView\.vue/, '兜底路由必须指向 NotFoundView');
});

test('404 视图自身带 <h1>（它不套布局，没有布局代它出标题）', () => {
  const text = read('views/NotFoundView.vue');
  assert.match(text, /<h1\b/, '404 页面缺少 h1');
});

test('两套外壳都挂跳转链接，且 <main> 能接住焦点', () => {
  for (const rel of LAYOUTS) {
    const text = read(rel);
    assert.match(text, /<SkipLink\b/, `${rel} 未挂 SkipLink`);
    const main = text.match(/<main\b((?:[^>"]|"[^"]*")*)>/);
    assert.ok(main, `${rel} 找不到 <main>`);
    assert.match(main[1], /id="main-content"/, `${rel} 的 <main> 缺 id="main-content"`);
    assert.match(main[1], /tabindex="-1"/, `${rel} 的 <main> 缺 tabindex="-1"（不可聚焦则接不住焦点）`);
  }
});

test('跳转链接必须自己接管点击（裸锚点会被 vue-router 当成路由跳转）', () => {
  const text = read('components/base/SkipLink.vue');
  assert.match(text, /@click\.prevent/, 'SkipLink 未阻止默认锚点行为 → 在子路由上点击会跳回首页');
  assert.match(text, /getElementById/, 'SkipLink 未显式聚焦目标元素');
});

test('闸自检：判据能真的命中', () => {
  const hasCatchAll = (t) => /pathMatch/.test(t);
  const hasSkip = (t) => /<SkipLink\b/.test(t);
  assert.equal(hasCatchAll("path: '/:pathMatch(.*)*'"), true);
  assert.equal(hasCatchAll("path: '/docs'"), false);
  assert.equal(hasSkip('<SkipLink />'), true);
  assert.equal(hasSkip('<!-- SkipLink -->'), false);
});
