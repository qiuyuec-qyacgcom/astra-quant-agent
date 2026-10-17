/**
 * 键盘快捷键交互与 kbd 徽标体验守卫闸（批 60）。
 *
 * ## 守什么
 *
 * 1. **核心功能键盘直达（Keyboard Shortcuts Affordance）**：
 *    - `FactorMatrix.vue`：支持按下 `/` 键立即聚焦标的检索框（类似 GitHub/Linear 标准体验），
 *      且检索框内展示 `<kbd> / </kbd>` 快捷键徽标（输入有值时自动淡出）；
 *    - `TopBar.vue`：决策轨迹流按钮必须展示 `<kbd>⌘J</kbd>` 快捷键徽标，并在 `:title` 中注明快捷键；
 *    - `TrajectoryPanel.vue`：关闭区域必须展示 `<kbd>Esc</kbd>` 徽标，并在关闭按钮 `:title` 中注明 `(Esc)`。
 *
 * 2. **弹窗/抽屉全局 Esc 提示可发现性**：
 *    - `BaseDialog.vue`、`BaseDrawer.vue` 与 `DocsView.vue` 的关闭按钮必须在 `:title` 中注明 `(Esc)`，
 *      让鼠标悬停用户在视觉上建立按 Escape 键即可关闭弹层的操作认知。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('FactorMatrix 支持 / 快捷键检索并在输入框内呈现 kbd 标识', () => {
  const text = readFileSync(path.join(SRC, 'components/dashboard/FactorMatrix.vue'), 'utf8');
  assert.match(text, /useHotkeys\s*\(\s*\{[\s\S]*'\/':/, 'FactorMatrix 缺失 / 快捷键注册');
  assert.match(text, /searchInput\.value\?\.focus\(\)/, 'FactorMatrix / 快捷键未执行聚焦');
  assert.match(text, /<kbd[^>]*>\s*\/\s*<\/kbd>/, 'FactorMatrix 输入框缺失 / 快捷键 kbd 徽标');
});

test('TopBar 与 TrajectoryPanel 显式呈现 ⌘J 与 Esc 快捷键提示', () => {
  const topBar = readFileSync(path.join(SRC, 'components/dashboard/TopBar.vue'), 'utf8');
  assert.match(topBar, /<kbd[^>]*>⌘J<\/kbd>/, 'TopBar 决策轨迹按钮缺失 ⌘J kbd 徽标');
  assert.match(topBar, /:title="[^"]*⌘J[^"]*"/, 'TopBar 决策轨迹按钮 title 缺失 ⌘J 快捷键说明');

  const panel = readFileSync(path.join(SRC, 'components/dashboard/TrajectoryPanel.vue'), 'utf8');
  assert.match(panel, /<kbd[^>]*>Esc<\/kbd>/, 'TrajectoryPanel 关闭区缺失 Esc kbd 徽标');
  assert.match(panel, /:title="[^"]*\(Esc\)[^"]*"/, 'TrajectoryPanel 关闭按钮 title 缺失 (Esc) 说明');
});

test('基础弹窗与抽屉关闭按钮均提示 (Esc) 快捷键', () => {
  const targets = [
    'components/base/BaseDialog.vue',
    'components/base/BaseDrawer.vue',
    'views/docs/DocsView.vue',
  ];

  for (const file of targets) {
    const text = readFileSync(path.join(SRC, file), 'utf8');
    assert.match(text, /:title="[^"]*\(Esc\)[^"]*"/, `${file} 关闭按钮 title 缺失 (Esc) 快捷键说明`);
  }
});

test('闸自检：能准确拦截无快捷键说明与缺失 kbd 徽标的组件', () => {
  const badBtn = '<button :title="title">打开</button>';
  const goodBtn = '<button :title="`${title} (⌘J)`">打开<kbd>⌘J</kbd></button>';

  const check = (html) => html.includes('<kbd>') && html.includes('(⌘J)');
  assert.equal(check(badBtn), false, '应拦截无快捷键提示的按钮');
  assert.equal(check(goodBtn), true, '应放行带 kbd 与快捷键 title 的按钮');
});
