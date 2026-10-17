/**
 * 浮层的焦点交接与 Escape —— 声明了 `role="dialog"` 就得配得上
 *
 * ## 批 104 修掉的两类问题
 *
 * 1. **打开后焦点没进面板**：⚙ 偏好设置、指标下拉都是非模态气泡，
 *    实测用键盘 Enter 打开后焦点**仍停在顶栏触发按钮上** ——
 *    读屏不会播报"对话框已打开"，键盘用户还得 Tab 才进得去
 *    （而面板恰好排在触发器后面只是 DOM 顺序上的巧合）。
 * 2. **按 Escape 关不掉**：这两个下拉此前**只能靠点空白处关**。
 *    指标下拉声明的就是 `role="dialog"` —— WAI-ARIA 要求 Escape 关闭对话框；
 *    选币下拉是 listbox，同样该支持。ChartWorkstation 里两个都不响应 Escape。
 *
 * ## 判据在盯什么
 *
 * 1. 声明了浮层角色（`dialog`/`listbox`/`alertdialog`/`menu`）的文件，
 *    必须**真的调用**两个焦点组合式之一（`useModalFocus` 模态 / `usePopoverFocus` 非模态）；
 * 2. 用 `usePopoverFocus` 的文件必须**自己处理 Escape** —— 那个组合式是故意不管 Escape 的
 *    （非模态气泡的关闭方式由调用方决定），所以漏了就是"打开后关不掉"；
 * 3. 面板必须有 `tabindex="-1"`，否则 `panel.focus()` 对 `<div>` **静默无效** ——
 *    这正是"看起来写了焦点管理、其实没生效"的常见原因。
 *
 * ## ⚠️ 必须先剥注释
 *
 * `useModalFocus` / `usePopoverFocus` 的空名字符串会**出现在注释里**
 * （两份文件都在注释里提到"所以不用 useModalFocus"）。
 * 只做 `src.includes('useModalFocus')` 会被注释骗过 —— 本文件第一版就是这么写的，
 * 于是"注释一留、调用删掉"的变异不会翻红。剥掉注释后再匹配调用形式 `useXxxFocus(`。
 *
 * ## 覆盖边界
 *
 * 判据只能确认「**接上了** Escape 处理」，**不能确认它真的关得掉** ——
 * 把处理函数第一行改成 `if (true) return` 这类短路，静态是看不出来的（实测该变异保持绿色）。
 * 所以「关得掉」这一条由**实机验证**兜底：批 104 实测两个下拉
 * （`role="dialog"` 的指标、`role="listbox"` 的选币）按 Escape 都能关且焦点回到触发器。
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

/** 去掉 JS 注释（含行注释与块注释），避免"注释里提过"被当成"真的用了"。 */
export function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const POPUP_ROLES = /role="(dialog|listbox|alertdialog|menu)"/;

/** 所有声明了浮层角色的 .vue 文件。 */
export function popupFiles() {
  return walk(SRC)
    .filter((f) => f.endsWith('.vue'))
    .map((f) => ({ file: f, rel: path.relative(SRC, f), raw: readFileSync(f, 'utf8') }))
    .filter((x) => POPUP_ROLES.test(x.raw));
}

/** 模板里带浮层角色、且带 `ref=` 的面板：`{rel, ref}`；没有 ref 的记为 null。 */
function popupPanels() {
  const out = [];
  for (const { rel, raw } of popupFiles()) {
    for (const m of raw.matchAll(/<(\w[\w-]*)\b([^>]*?)>/gs)) {
      const attrs = m[2];
      if (!POPUP_ROLES.test(attrs)) continue;
      const r = /ref="([\w-]+)"/.exec(attrs);
      out.push({ rel, ref: r ? r[1] : null });
    }
  }
  return out;
}

test('**每一个**浮层面板都要被焦点组合式接管（一个文件里有俩就得俩都接）', () => {
  // ⚠️ 不能只按文件粒度查：「文件里出现过 usePopoverFocus(」是不够的 ——
  // ChartWorkstation 里有**两个**气泡，删掉其中一个的调用，文件粒度照样是绿的
  //（第一版就是这个漏洞，M37 变异没翻红）。所以按**面板 ref** 逐个对。
  const panels = popupPanels();
  assert.ok(panels.length >= 6, `扫到的浮层面板太少（${panels.length}），判据可能失配`);
  const missing = [];
  for (const { rel, ref } of panels) {
    if (!ref) {
      missing.push(`${rel}（浮层面板没有 ref，无法被聚焦 —— 补 ref="x" 再交给焦点组合式）`);
      continue;
    }
    const code = stripComments(readFileSync(path.join(SRC, rel), 'utf8'));
    const wired = new RegExp(`use(?:Modal|Popover)Focus\\s*\\(\\s*${ref}\\b`).test(code);
    if (!wired) missing.push(`${rel} → ref="${ref}"`);
  }
  assert.deepEqual(
    missing,
    [],
    `这些浮层打开后焦点不会进面板、关闭后也不会归还：\n  ${missing.join('\n  ')}\n` +
      '模态用 useModalFocus(panelRef, close)，非模态气泡用 usePopoverFocus(panelRef, triggerRef, openRef)。',
  );
});

test('非模态气泡（usePopoverFocus）必须自己处理 Escape', () => {
  const missing = [];
  for (const { rel, raw } of popupFiles()) {
    const code = stripComments(raw);
    if (!/usePopoverFocus\s*\(/.test(code)) continue;
    // usePopoverFocus 故意不管 Escape，所以文件里必须自己有一处 Escape 处理
    if (!/['"]Escape['"]/.test(code)) missing.push(rel);
  }
  assert.deepEqual(
    missing,
    [],
    `这些气泡打开后按 Escape 关不掉（usePopoverFocus 不管 Escape，得调用方自己接）：\n  ${missing.join('\n  ')}`,
  );
});

test('气泡面板必须有 tabindex="-1"（否则 panel.focus() 静默无效）', () => {
  const missing = [];
  for (const { rel, raw } of popupFiles()) {
    const code = stripComments(raw);
    if (!/usePopoverFocus\s*\(/.test(code)) continue;
    if (!/tabindex="-1"/.test(raw)) missing.push(rel);
  }
  assert.deepEqual(
    missing,
    [],
    `这些文件用了 usePopoverFocus，却没有 tabindex="-1" 的面板 —— ` +
      `对 <div> 调 .focus() 是不生效的，焦点交接会悄悄失效：\n  ${missing.join('\n  ')}`,
  );
});

test('判据自检：注释里的组合式名字不算「用了」', () => {
  const fake = '/* 这里提到 usePopoverFocus 但没调用 */\nconst x = 1;';
  assert.equal(/usePopoverFocus\s*\(/.test(stripComments(fake)), false);
  assert.equal(/usePopoverFocus\s*\(/.test(stripComments('const { release } = usePopoverFocus(panel, trigger, open);')), true);
});
