/**
 * Tab 顺序工具（批 111）。
 *
 * ## 为什么需要它
 *
 * **正数 `tabindex` 会插队**：`tabindex="1"` 的元素排到所有 `tabindex="0"`
 * 之前 —— 包括各页顶部的「跳到主内容」。第三方图表库 klinecharts 恰恰这么干
 * （dist 里就是一行 `this._chartContainer.tabIndex = 1`），于是键盘用户
 * 第一次 Tab 落在**没有名字、没有焦点环**的画布上（实测 895×379、`outline: none`），
 * WCAG 2.4.1 的旁路机制被抢位。`/` 与 `/trading` 两页都能复现。
 *
 * 我们改不了 `node_modules`，所以在**挂载之后**把图表子树里的正数 tabindex
 * 归一化掉。只动正数，`0` 与 `-1` 一律不碰。
 *
 * 抽成独立模块是为了**能被单测**：入参只要求「有 tabIndex、能 querySelectorAll」
 * 这两件事，不需要真实 DOM。
 */

interface TabIndexHost {
  tabIndex: number;
  querySelectorAll: (selector: string) => ArrayLike<{ tabIndex: number }>;
}

/**
 * 把根元素及其后代里的**正数** `tabIndex` 改成 `-1`（从 Tab 顺序里摘掉）。
 * @returns 实际改动的元素个数（便于调用方/测试断言"确实动过手"）。
 */
export function demotePositiveTabIndex(root: TabIndexHost | null | undefined): number {
  if (!root) return 0;
  let changed = 0;
  if (root.tabIndex > 0) {
    root.tabIndex = -1;
    changed += 1;
  }
  const nodes = root.querySelectorAll('[tabindex]');
  for (let i = 0; i < nodes.length; i += 1) {
    const el = nodes[i];
    if (el.tabIndex > 0) {
      el.tabIndex = -1;
      changed += 1;
    }
  }
  return changed;
}
