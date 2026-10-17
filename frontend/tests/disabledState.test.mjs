/**
 * 禁用态覆盖 —— 「能禁用的控件，禁用时必须看得出来」
 *
 * ## 为什么单独建一条闸
 *
 * 全站有 **131 个**绑定了 `:disabled` / `:aria-disabled` 的控件（提交中、加载中、
 * 表单未通过校验……）。批 102 量下来 **130 个已有禁用样式**，只漏了 1 个：
 * `OverviewPage` 的刷新按钮 —— 它在 `loading` 期间禁用，**恰恰是用户最想再点一次的时候**，
 * 却长得和可点的一模一样，`:hover` 也照样亮（点下去没有任何反应）。
 *
 * 更隐蔽的是第二半：**`.x:hover` 没有排除 `:disabled`**。
 * 仓库里 `.btn-*:hover:not(:disabled)`、`.cn-mode:hover:not(:disabled)` 已是很普遍的写法，
 * 说明这是既有约定 —— 但约定不能靠自觉，得让判据盯着。
 *
 * ## 判据在做三件事
 *
 * 1. 扫出所有 CSS 里「有 `:disabled` 规则」的类；
 * 2. 扫出所有绑了禁用态的模板元素，逐个检查它**至少有一个类**被 (1) 覆盖，
 *    或者自己带了 Tailwind 的 `disabled:` 工具类；
 * 3. 检查这些可禁用类的 `:hover` 规则是否都写了 `:not(:disabled)`。
 *
 * ## 已知边界
 *
 * - 组件内部自行处理禁用态的（如 `BaseSwitch` 内部用 `.switch`，后者有 `:switch:disabled`）
 *   通过 `COMPONENT_INTERNAL` 显式豁免，且豁免会做「不空转」校验。
 * - 只认**类选择器**；靠标签选择器（如 `input:disabled`）覆盖的按类名匹配不到，
 *   但本仓库的控件都用类，实际不会漏。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function walk(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

/** 组件内部自己处理禁用态的（每条都要能说清在哪处理）。 */
export const COMPONENT_INTERNAL = [
  {
    tag: 'BaseSwitch',
    why: 'BaseSwitch 内部渲染 class="switch"，而 `components.css` 有 `.switch:disabled`。',
    check: ['components/base/BaseSwitch.vue', 'switch'],
  },
];

const styleOf = (file) => {
  const raw = readFileSync(file, 'utf8');
  if (!file.endsWith('.vue')) return raw;
  return [...raw.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
};

/** 收集「带 :disabled 规则」的类名，以及「带 :hover 规则」的 (类, 选择器) 对。 */
export function cssIndex() {
  const disabledClasses = new Set();
  const hoverRules = [];
  const files = walk(SRC);
  for (const f of files) {
    if (!f.endsWith('.css') && !f.endsWith('.vue')) continue;
    // ⚠️ 必须先剥注释：注释文本里常有 `{`，朴素正则会把注释当成选择器
    // （本文件第一版就因此把一段讲解 `.switch:hover` 的注释报成了违规）。
    const css = styleOf(f).replace(/\/\*[\s\S]*?\*\//g, '');
    if (!css.trim()) continue;
    for (const m of css.matchAll(/([^{}]+)\{/g)) {
      const sel = m[1].trim();
      if (sel.startsWith('@')) continue;
      const cls = [...sel.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((x) => x[1]);
      // ⚠️ 先剥掉 `:not(...)`：`.x:hover:not(:disabled)` 里的 `:disabled` 是**反向**的，
      // 把它当成「有禁用样式」会让判据完全失效 —— 本文件第一版就栽在这里
      //（删掉 `.ov-btn-refresh:disabled` 后判据仍然是绿的）。
      const bare = sel.replace(/:not\([^)]*\)/g, '');
      if (/:disabled\b/.test(bare) || /\[disabled\]/.test(bare) || /aria-disabled/.test(bare)) {
        for (const c of cls) disabledClasses.add(c);
      }
      if (/:hover\b/.test(sel)) hoverRules.push({ file: path.relative(SRC, f), sel, cls });
    }
  }
  return { disabledClasses, hoverRules };
}

test('能禁用的控件必须有禁用样式（全站 131 个，批 102 补上最后 1 个）', () => {
  const { disabledClasses } = cssIndex();
  const missing = [];
  for (const f of walk(SRC)) {
    if (!f.endsWith('.vue')) continue;
    const src = readFileSync(f, 'utf8');
    for (const m of src.matchAll(/<(\w[\w-]*)\b([^>]*?)\/?>/gs)) {
      const [, tag, attrs] = m;
      if (!/:disabled=|:aria-disabled=/.test(attrs)) continue;
      const cls = (/\bclass="([^"]*)"/.exec(attrs) || [, ''])[1];
      if (cls.includes('disabled:')) continue;                      // Tailwind 工具类
      if (COMPONENT_INTERNAL.some((c) => c.tag === tag)) continue;  // 组件内部处理
      const own = cls.split(/\s+/).filter((c) => c && !c.startsWith('[') && !c.startsWith('{'));
      if (own.some((c) => disabledClasses.has(c))) continue;
      missing.push(`${path.relative(SRC, f)}:${src.slice(0, m.index).split('\n').length}  <${tag} class="${cls.slice(0, 48)}">`);
    }
  }
  assert.deepEqual(
    missing,
    [],
    `这些控件被禁用后外观不变（用户会以为还能点）：\n  ${missing.join('\n  ')}\n` +
      '请加 .x:disabled { opacity: .4; cursor: not-allowed }（同 .btn:disabled）。',
  );
});

test('可禁用类的 :hover 必须排除 :disabled（否则禁用时悬停还会亮）', () => {
  const { disabledClasses, hoverRules } = cssIndex();
  const bad = [];
  for (const { file, sel, cls } of hoverRules) {
    if (/:not\(:disabled\)/.test(sel) || /:disabled/.test(sel)) continue;
    // 只有当这条 hover 直接作用于那个「可被禁用的类」时才算违规
    const direct = cls.some((c) => disabledClasses.has(c) && new RegExp(`\\.${c}:hover`).test(sel));
    if (direct) bad.push(`${file}  ${sel}`);
  }
  assert.deepEqual(
    bad,
    [],
    `这些 hover 在控件被禁用时依然生效：\n  ${bad.join('\n  ')}\n改成 .x:hover:not(:disabled) 。`,
  );
});

test('组件内部豁免不得空转（组件改了就把豁免删掉）', () => {
  for (const c of COMPONENT_INTERNAL) {
    const [file, needle] = c.check;
    const src = readFileSync(path.join(SRC, file), 'utf8');
    assert.ok(src.includes(needle), `豁免已失效：${file} 里找不到 ${needle} —— 请更新 COMPONENT_INTERNAL`);
  }
});
