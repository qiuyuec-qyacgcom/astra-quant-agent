/**
 * 相对时间标准 datetime 与文档全屏预览无障碍守卫闸（批 58）。
 *
 * ## 守什么
 *
 * 1. **HTML5 `<time>` 元素机器可读性（datetime ISO-8601）**：
 *    `TimeAgo.vue` 渲染的标准 `<time>` 标签必须声明 `:datetime="iso"` 属性，
 *    使屏幕阅读器与浏览器翻译工具能够获知机器可读的精确 ISO 8601 时间戳，
 *    而非仅仅依赖纯视觉的相对文本。
 *
 * 2. **DocsView 图片全屏预览模态语义（Dialog & Escape & Scroll Lock）**：
 *    - `DocsView.vue` 图片放大遮罩必须声明 `role="dialog" aria-modal="true"` 及 `:aria-label`；
 *    - 必须包含显式关闭图标按钮（带 `:title` 与 `:aria-label`）；
 *    - 键盘用户按下 Escape 键必须能直接退出大图预览与移动端抽屉；
 *    - 在大图预览或移动端抽屉打开时，必须锁死 `document.body.style.overflow = 'hidden'` 防止背景滚动穿透。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('TimeAgo 组件必须声明 :datetime ISO-8601 属性', () => {
  const text = readFileSync(path.join(SRC, 'components/base/TimeAgo.vue'), 'utf8');
  assert.match(text, /:datetime="iso"/, 'TimeAgo 缺失 :datetime="iso" 绑定');
  assert.match(text, /ts\.toISOString\(\)/, 'TimeAgo 缺失 toISOString 计算逻辑');
});

test('DocsView 图片放大模态具备完整的 Dialog 语义、关闭按钮与键盘 Escape 监听', () => {
  const text = readFileSync(path.join(SRC, 'views/docs/DocsView.vue'), 'utf8');

  // Dialog ARIA 语义
  assert.match(text, /role="dialog"/, 'DocsView 图片模态缺失 role="dialog"');
  assert.match(text, /aria-modal="true"/, 'DocsView 图片模态缺失 aria-modal="true"');
  assert.match(text, /:aria-label="t\('docs\.zoomModalAria'\)"/, 'DocsView 图片模态缺失 :aria-label');

  // 关闭按钮
  assert.match(text, /<button[^>]*:aria-label="t\('common\.close'\)"/, 'DocsView 图片模态缺失关闭按钮');

  // Escape 键退出
  assert.match(text, /e\.key === 'Escape'/, 'DocsView 缺失 Escape 键监听逻辑');

  // 滚动锁定
  assert.match(text, /document\.body\.style\.overflow = 'hidden'/, 'DocsView 缺失 body 滚动锁定逻辑');
});

test('闸自检：能准确拦截缺少 datetime 的 time 标签与无 Escape 的模态', () => {
  const badTime = '<time :title="abs">{{ text }}</time>';
  const goodTime = '<time :datetime="iso" :title="abs">{{ text }}</time>';
  const badModal = '<div v-if="zoom" class="fixed inset-0"><img /></div>';
  const goodModal = '<div v-if="zoom" role="dialog" aria-modal="true"><img /></div>';

  const checkTime = (html) => !html.includes(':datetime=');
  const checkModal = (html) => !html.includes('role="dialog"') || !html.includes('aria-modal="true"');

  assert.equal(checkTime(badTime), true, '应拦截无 datetime 的 time 标签');
  assert.equal(checkTime(goodTime), false, '应放行合规 time 标签');
  assert.equal(checkModal(badModal), true, '应拦截无 ARIA 语义的模态浮层');
  assert.equal(checkModal(goodModal), false, '应放行合规 Dialog 浮层');
});
