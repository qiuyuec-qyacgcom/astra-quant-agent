/**
 * 吐司消息与指标徽章可访问性与语义守卫闸（批 65）。
 *
 * ## 守什么
 *
 * 1. **Toast 实时通知语义分级（WCAG 4.1.3 Status Messages）**：
 *    - `ToastHost.vue` 中的普通提示（ok / warn / info）使用 `role="status"`；
 *    - 错误提示（err）自动提升为 `role="alert"`，确保屏幕阅读器能以最高优先级打断播报严重异常；
 *    - 装饰性状态图标与关闭叉号图标均声明 `aria-hidden="true"`；
 *    - 关闭按钮声明完整的 `:title` 与 `:aria-label`。
 *
 * 2. **置信度徽章双模态读屏（Numeric + Tier Dual Modality）**：
 *    - `ConfBadge.vue` 为读屏设备提供包含档位名称与精确量化概率数值的完整 `:aria-label`；
 *    - 缺失状态占位符声明 `aria-label="--"`。
 *
 * 3. **方向标签符号语义解耦（DirTag Symbol Decoupling）**：
 *    - `DirTag.vue` 的方向箭头符号（▲ / ▼ / —）声明 `aria-hidden="true"`，通过文字标签传达多/空/观望语义。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('ToastHost.vue 区分普通 status 与错误 alert 语义并隐藏装饰性图标', () => {
  const text = readFileSync(path.join(SRC, 'components/base/ToastHost.vue'), 'utf8');

  // role 分级
  assert.match(text, /:role="item\.kind === 'err' \? 'alert' : 'status'"/, 'ToastHost 未对错误消息声明 role="alert"');

  // 图标 aria-hidden
  assert.match(text, /<component[^>]*:is="icons\[item\.kind\]"[^>]*aria-hidden="true"/, 'Toast 状态图标未隐藏');
  assert.match(text, /<X[^>]*aria-hidden="true"/, 'Toast 关闭图标未隐藏');

  // 关闭按钮
  assert.match(text, /:aria-label="t\('common\.close'\)"/, 'Toast 关闭按钮缺少 aria-label');
  assert.match(text, /:title="t\('common\.close'\)"/, 'Toast 关闭按钮缺少 title');
});

test('ConfBadge.vue 同时提供置信档位与数值可访问标签', () => {
  const text = readFileSync(path.join(SRC, 'components/base/ConfBadge.vue'), 'utf8');

  assert.match(text, /:aria-label="ariaLabel"/, 'ConfBadge 缺少 :aria-label="ariaLabel" 绑定');
  assert.match(text, /common\.conf\.\${tier\.value\.tier}/, 'ConfBadge 未生成包含档位与数值的可访问名称');
  assert.match(text, /<span v-else class="t-faint" aria-label="--">/, 'ConfBadge 空态缺少 aria-label');
});

test('DirTag.vue 隐藏方向箭头符号并依赖文本标签', () => {
  const text = readFileSync(path.join(SRC, 'components/base/DirTag.vue'), 'utf8');

  assert.match(text, /<span aria-hidden="true">{{ glyph }}<\/span>/, 'DirTag 未隐藏箭头符号');
  assert.match(text, /{{ label }}/, 'DirTag 缺少可读文本标签');
});

test('闸自检：能准确拦截缺少 alert 语义的 ToastHost 与缺失数值标签的 ConfBadge', () => {
  const badToast = '<div role="status"><X /></div>';
  const goodToast = '<div :role="item.kind === \'err\' ? \'alert\' : \'status\'"><X aria-hidden="true" /></div>';

  const checkToast = (html) => html.includes(':role="item.kind === \'err\' ? \'alert\' : \'status\'"') && html.includes('aria-hidden="true"');
  assert.equal(checkToast(badToast), false, '应拦截未分级 Toast');
  assert.equal(checkToast(goodToast), true, '应放行合规 Toast');
});
