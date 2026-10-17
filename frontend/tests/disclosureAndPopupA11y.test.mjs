/**
 * 折叠展开与弹层组件无障碍语义守卫闸（批 54）。
 *
 * ## 守什么
 *
 * 1. **aria-expanded 与 aria-controls 强绑定（WAI-ARIA Disclosure APG）**：
 *    全站所有带有 `:aria-expanded` 或 `aria-expanded` 的交互触发器（按钮、折叠行、抽屉开关），
 *    必须同时声明 `:aria-controls` 或 `aria-controls`，建立控制端与受控目标 ID 的显式引用，
 *    彻底杜绝「知道展开了却不知道展开了哪里」的无障碍断裂。
 *
 * 2. **SettingsPopover 弹层角色收敛（杜绝非法 ARIA 嵌套）**：
 *    `SettingsPopover.vue` 内部包含语言切换分段（`role="tablist"`）与色盲模式开关（`role="switch"`），
 *    严禁声明为 `role="menu"`（W3C 规范禁止 menu 包含 tablist/switch 等表单原语）；
 *    必须声明为带有 `aria-label` 的 `role="dialog"`，且触发按钮声明 `aria-haspopup="dialog"`。
 *
 * 3. **行情工作台浮层角色**：
 *    `ChartWorkstation.vue` 的标的下拉声明 `role="listbox"` / `aria-haspopup="listbox"`，
 *    指标配置浮层声明 `role="dialog"` / `aria-haspopup="dialog"`。
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

test('全站所有 aria-expanded 元素必须声明 aria-controls 引用', () => {
  const badTriggers = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    if (!body.includes('aria-expanded')) continue;

    for (const m of body.matchAll(/<([a-zA-Z][\w.-]*)\b([^>]*)>/g)) {
      const tag = m[1];
      const attrs = m[2];
      if (attrs.includes('aria-expanded')) {
        const hasControls = attrs.includes('aria-controls') || attrs.includes(':aria-controls');
        if (!hasControls) {
          const line = text.slice(0, tm.index).split('\n').length + body.slice(0, m.index).split('\n').length;
          badTriggers.push(`${rel}:${line} <${tag}> 声明了 aria-expanded 但缺失 aria-controls`);
        }
      }
    }
  }

  assert.deepEqual(badTriggers, [], `发现未绑定受控目标的展开触发器：\n  ${badTriggers.join('\n  ')}`);
});

test('SettingsPopover 必须声明 role="dialog" 且禁止非法 role="menu"', () => {
  const text = readFileSync(path.join(SRC, 'components/dashboard/SettingsPopover.vue'), 'utf8');
  assert.equal(/role="menu"/.test(text), false, 'SettingsPopover 不得包含 role="menu"（禁止将 tablist 嵌套进 menu）');
  assert.match(text, /role="dialog"/, 'SettingsPopover 缺失 role="dialog"');
  assert.match(text, /aria-haspopup="dialog"/, 'SettingsPopover 触发器缺失 aria-haspopup="dialog"');
  // 批 85：受控目标 `<div v-if="open">` 收起时不在 DOM —— 因此 aria-controls
  // 必须**条件输出**（目标存在时才给），否则引用悬空。故断言「引用了 panelId」
  // 且「是带 undefined 的条件式」，而不是钉死无条件的旧写法。
  assert.match(text, /:aria-controls="[^"]*panelId[^"]*"/, 'SettingsPopover 缺失 :aria-controls 对 panelId 的引用');
  assert.match(
    text,
    /:aria-controls="[^"]*\?[^"]*undefined[^"]*"/,
    'SettingsPopover 的 aria-controls 必须条件输出（其目标是 v-if 渲染，收起时不在 DOM）',
  );
});

test('ChartWorkstation 浮层具备正确的 popover ARIA 语义', () => {
  const text = readFileSync(path.join(SRC, 'components/dashboard/ChartWorkstation.vue'), 'utf8');
  assert.match(text, /aria-haspopup="listbox"/, '标的菜单缺失 aria-haspopup="listbox"');
  assert.match(text, /role="listbox"/, '标的浮层缺失 role="listbox"');
  assert.match(text, /aria-haspopup="dialog"/, '指标菜单缺失 aria-haspopup="dialog"');
  assert.match(text, /role="dialog"/, '指标浮层缺失 role="dialog"');
});

test('闸自检：能准确拦截孤立 aria-expanded 与非法 role="menu"', () => {
  const badTrigger = '<button :aria-expanded="open">Toggle</button>';
  const goodTrigger = '<button :aria-expanded="open" :aria-controls="panelId">Toggle</button>';
  const badPopover = '<div role="menu"><div role="tablist"></div></div>';

  const checkTrigger = (html) => html.includes('aria-expanded') && !html.includes('aria-controls');
  const checkPopover = (html) => html.includes('role="menu"') && html.includes('role="tablist"');

  assert.equal(checkTrigger(badTrigger), true, '应拦截缺少 aria-controls 的触发器');
  assert.equal(checkTrigger(goodTrigger), false, '应放行配对的触发器');
  assert.equal(checkPopover(badPopover), true, '应拦截在 menu 中嵌套 tablist 的非法 ARIA 结构');
});
