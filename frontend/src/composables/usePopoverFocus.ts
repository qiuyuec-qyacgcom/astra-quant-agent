/**
 * 非模态浮层的焦点交接（批 104）
 *
 * ## 为什么不能直接用 `useModalFocus`
 *
 * `useModalFocus` 是**模态**用的：Tab 环绕不逃到背景、锁背景滚动、Escape 入栈。
 * 而 ⚙ 偏好设置、指标下拉这类**气泡**是**非模态**的 ——
 * 它们没有遮罩、允许点背景、也不该把用户锁在 Tab 序列里。
 * 所以两者不能共用：一个要"关住"，一个只要"接一下手"。
 *
 * ## 只做一件事：打开时把焦点交给面板，关闭时还给触发器
 *
 * 批 104 之前这两个气泡都**没有**这一步，实测（键盘 Enter 打开 ⚙）：
 *
 * - 焦点仍停在顶栏的触发按钮上 —— 读屏不会播报"对话框已打开"，
 *   键盘用户要按 Tab 才能进面板，而面板就在触发器**后面**这一点纯属 DOM 顺序上的巧合；
 * - 面板 `role="dialog"` 是**声明了**的，声明了就该配得上。
 *
 * 顺带一个容易漏的点：面板必须是 `tabindex="-1"`，
 * 否则 `panel.focus()` 对 `<div>` **静默无效**（这正是很多"看起来写了焦点管理"却不起作用的原因）。
 *
 * ## 为什么关闭时"要有条件地"还焦点
 *
 * 只有焦点**当前还在面板里**时才还给触发器。否则用户点了别处（或程序化移动了焦点），
 * 我们再抢回来就是"焦点乱跳"—— 比不还更糟。
 */
import { nextTick, watch, type Ref } from 'vue';

export function usePopoverFocus(
  panel: Ref<HTMLElement | null>,
  trigger: Ref<HTMLElement | null>,
  isOpen: Ref<boolean>,
) {
  /** 焦点是否在面板里 —— 关闭时用它决定"该不该还"。 */
  function focusInside(): boolean {
    const p = panel.value;
    const a = document.activeElement;
    return !!p && !!a && p.contains(a);
  }

  watch(isOpen, async (open, wasOpen) => {
    if (open && !wasOpen) {
      await nextTick();
      panel.value?.focus?.();
    } else if (!open && wasOpen) {
      if (focusInside()) trigger.value?.focus?.();
    }
  });

  /** 组件卸载时兜底：面板没了，把焦点还给触发器（否则焦点掉到 body，Tab 从头开始）。 */
  function release() {
    if (focusInside()) trigger.value?.focus?.();
  }

  return { release };
}
