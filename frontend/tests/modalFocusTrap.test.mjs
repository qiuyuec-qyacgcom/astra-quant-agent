/**
 * 模态**焦点陷阱**的可 Tab 判据守卫闸（批 109）。
 *
 * ## 实测出来的真缺陷
 *
 * `useModalFocus` 里「循环 Tab」的写法是：取面板内可聚焦元素列表，
 * 焦点在 `els[last]` 上再按 Tab 就 `preventDefault()` 并跳到 `els[0]`。
 * 但那份列表来自选择器
 * `a[href],button:not([disabled]),input:not([disabled]),…,[tabindex]:not([tabindex="-1"])`
 * —— 其中 **`button:not([disabled])` 会把 `tabindex="-1"` 的按钮也算进来**，
 * 而浏览器根本不会把 Tab 焦点给这种按钮。
 *
 * 站上到处是这种写法：`BaseTabs` 用**漫游 tabindex**（只有当前页签能 Tab，
 * 其余 `tabindex="-1"` 靠方向键切）。于是：
 *
 * - 记进来的 `last` 是那个**走不到的非活动页签**；
 * - `document.activeElement === last` 永远不成立 → **不拦 Tab**；
 * - 浏览器把焦点送到面板后面的 `BODY`（背景页）。
 *
 * 实机复现（批 109 修前，轨迹抽屉，面板内选择器命中 3 个）：
 *
 * ```
 * 打开后 → PANEL(容器)
 * Tab 1 → 关闭按钮         ✅
 * Tab 2 → 当前页签         ✅
 * Tab 3 → BODY            ❌ 逃逸
 * Tab 4 → 关闭按钮  … 每 3 次逃逸 1 次
 * ```
 *
 * 一个声明了 `aria-modal="true"` 的抽屉，键盘用户能 Tab 到背景页 —— 语义在说谎。
 * 修法：判据加一层 **`el.tabIndex >= 0`**（抽成可单测的 `tabbableOf`）。
 * 修后实机：`选择器命中 3 / 真正可 Tab 2`，**Tab×9 逃逸 0 次**；
 * 台账抽屉、归档对话框、拦截器对话框、决策轨迹抽屉均 0 逃逸。
 *
 * ## 为什么把判据抽成函数
 *
 * 这样这条规则能被**当行为测**（见下），而不是只靠 grep 一个字符串。
 * 抽的时候刻意不依赖 DOM：泛型只要求 `{ tabIndex, offsetParent }`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { tabbableOf, lockBodyScroll, unlockBodyScroll, getScrollLockDepth } from '../src/composables/useModalFocus.ts';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
/** 造一个「元素形状」的假对象：只看得到 tabIndex 与 offsetParent。 */
const el = (name, tabIndex, hidden = false) => ({ name, tabIndex, offsetParent: hidden ? null : {} });

test('tabIndex = -1 的控件不算可 Tab（漫游 tabindex / tabindex="-1"）', () => {
  const list = [el('关闭按钮', 0), el('非活动页签', -1), el('当前页签', 0)];
  assert.deepEqual(tabbableOf(list).map((x) => x.name), ['关闭按钮', '当前页签']);
});

test('隐藏元素（offsetParent === null）不算可 Tab', () => {
  const list = [el('可见按钮', 0), el('display:none 里的按钮', 0, true), el('隐藏的 -1', -1, true)];
  assert.deepEqual(tabbableOf(list).map((x) => x.name), ['可见按钮']);
});

test('回归：非活动页签不能成为「最后一个」——否则循环 Tab 不生效、焦点逃到背景页', () => {
  // 复刻轨迹抽屉实测的名单顺序：关闭按钮、非活动页签、当前页签
  const list = [el('关闭按钮', 0), el('实时日志(非活动)', -1), el('AI 决策流(当前)', 0)];
  const last = tabbableOf(list).at(-1);
  assert.equal(last.name, 'AI 决策流(当前)', '最后一个可 Tab 元素算错了（会把焦点放出面板）');
  assert.notEqual(last.name, '实时日志(非活动)', '又把 -1 的页签当成 last 了 —— 这正是实测逃逸的根因');
});

test('tabIndex > 0（显式指定顺序）仍算可 Tab，且不改顺序', () => {
  const list = [el('a', 0), el('b', 2), el('c', 1)];
  assert.deepEqual(tabbableOf(list).map((x) => x.name), ['a', 'b', 'c']);
});

test('空列表不炸（面板内一个可聚焦元素都没有）', () => {
  assert.deepEqual(tabbableOf([]), []);
});

test('滚动锁引用计数：嵌套模态关闭时只有深度归零才解开 body 滚动锁（批 116）', () => {
  const origDoc = globalThis.document;
  try {
    const fakeBody = { style: { overflow: '' } };
    globalThis.document = { body: fakeBody };

    assert.equal(getScrollLockDepth(), 0);
    assert.equal(fakeBody.style.overflow, '');

    // 打开第一层模态
    lockBodyScroll();
    assert.equal(getScrollLockDepth(), 1);
    assert.equal(fakeBody.style.overflow, 'hidden');

    // 打开第二层嵌套模态（例如在历史弹窗里点回滚弹出 ConfirmHost）
    lockBodyScroll();
    assert.equal(getScrollLockDepth(), 2);
    assert.equal(fakeBody.style.overflow, 'hidden');

    // 关闭第二层模态：第一层仍然开着，body.overflow 必须保持 'hidden'，绝不能提前解开！
    unlockBodyScroll();
    assert.equal(getScrollLockDepth(), 1);
    assert.equal(fakeBody.style.overflow, 'hidden');

    // 关闭第一层模态：所有模态退出，body.overflow 恢复原始值 ''
    unlockBodyScroll();
    assert.equal(getScrollLockDepth(), 0);
    assert.equal(fakeBody.style.overflow, '');

    // 冗余调用 unlock 不得产生负数下溢
    unlockBodyScroll();
    assert.equal(getScrollLockDepth(), 0);
    assert.equal(fakeBody.style.overflow, '');
  } finally {
    globalThis.document = origDoc;
  }
});

test('useModalFocus 必须用 tabbableOf（别再退回只按 offsetParent 过滤的选择器）', () => {
  const src = readFileSync(path.join(SRC, 'composables/useModalFocus.ts'), 'utf8');
  assert.match(src, /export function tabbableOf/, '抽出来的判据不见了');
  assert.match(src, /focusables\(\)[\s\S]{0,220}tabbableOf\(/, 'focusables() 没有走 tabbableOf —— 漫游 tabindex 会再次逃逸');
  assert.match(src, /el\.tabIndex >= 0/, 'tabIndex 过滤被删了');
  // 陷阱的三件事必须都还在（别为了修这条把别的删了）
  assert.match(src, /e\.key === 'Escape'/, 'Escape 关闭逻辑不见了');
  assert.match(src, /e\.key !== 'Tab'/, 'Tab 处理逻辑不见了');
  assert.match(src, /export function lockBodyScroll/, '滚动锁引用计数函数不见了');
});
