/**
 * 模态焦点管理（批 42）—— 从 `BaseDialog.vue` / `BaseDrawer.vue` 里提取的**同一份逻辑**。
 *
 * ## 为什么要提出来
 *
 * 这三件事（Escape 关层、Tab 环绕不逃到背景页、打开/关闭时的焦点交接）此前在
 * BaseDialog 与 BaseDrawer 里**各写了一份**，而 `TrajectoryPanel.vue` —— 一个同样声明
 * `role="dialog" aria-modal="true"` 的模态抽屉 —— **一份都没有**：
 *
 * - 按 Escape 关不掉（全站唯一关不掉的模态）；实测复现；
 * - Tab 一路走到被遮罩盖住的背景页（`aria-modal` 说了谎）；
 * - 打开后焦点仍在顶栏的触发按钮上，关闭后也不归还；
 * - 背景页仍可滚动（两个基础组件都有滚动锁）。
 *
 * 抄第三份不是办法，所以提取到这里：
 * **同一层栈**（模块级 `stack`）保证嵌套时只有最上层响应 Escape，
 * 后来者只要接上本组合式就不会再出现"某个模态少一样"。
 *
 * ## 用法
 *
 * ```ts
 * const panel = ref<HTMLElement | null>(null)
 * const { sync } = useModalFocus(panel, () => emit('close'))
 * watch(() => props.open, sync, { immediate: true })
 * onBeforeUnmount(() => release())   // 组件卸载时兜底
 * ```
 */
import { nextTick, type Ref } from 'vue';

/** 可聚焦元素判据（三处统一）：可见性用 `offsetParent` 过滤隐藏项。 */
const FOCUSABLE_SELECTOR =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * 真正能被 **Tab 键**走到的元素（批 109 修）。
 *
 * ⚠️ 只靠 `FOCUSABLE_SELECTOR` 不够：`button:not([disabled])` 会**把
 * `tabindex="-1"` 的按钮也算进来** —— 而这类按钮浏览器根本不给它 Tab 焦点。
 * 站上到处都是这种写法（`BaseTabs` 的**漫游 tabindex**：只有当前页签可 Tab，
 * 其余 `tabindex="-1"` 靠方向键切换）。
 *
 * 后果是实测出来的真缺陷：焦点停在「当前页签」时，循环判据里算出的
 * `last` 是那个**走不到的非活动页签**，于是 `activeElement === last` 永远不成立、
 * **不拦 Tab**，浏览器就把焦点送到面板后面的 `BODY` ——
 * 声明了 `aria-modal="true"` 的抽屉，键盘用户能一路 Tab 到背景页去。
 * 实测轨迹抽屉：Tab 序列 `关闭 → 当前页签 → BODY → 回到关闭`，每 3 次逃逸 1 次。
 *
 * 判据改为按 **`el.tabIndex >= 0`** 过滤：漫游 tabindex、`tabindex="-1"`、
 * 以及将来任何"看着能聚焦其实不能"的写法都会被统一排除。
 */
export function tabbableOf<T extends { tabIndex: number; offsetParent: unknown }>(els: T[]): T[] {
  return els.filter((el) => el.offsetParent !== null && el.tabIndex >= 0);
}

/** 模块级栈：嵌套模态只有最上层响应 Escape。 */
const stack: symbol[] = [];

/**
 * 模块级滚动锁引用计数（批 116 修）。
 *
 * ⚠️ 不能把滚动锁简单写成布尔开关：
 * 历史弹窗上点"回滚"打开确认弹窗（ConfirmHost）、或在任何模态层里调用 `ask()` 时，
 * 会出现**嵌套模态**。如果每次关闭都简单执行 `body.style.overflow = ''`，
 * 那么上层的确认弹窗一旦关闭，底层的模态虽然**依然开着**，背景页的滚动锁却被提前解开，
 * 用户滚动时底层页面穿透滑动，遮罩与焦点脱节。
 *
 * 引入引用计数：
 * - 只有深度从 0 变 1 时才记录原值并锁死 `hidden`；
 * - 关闭时深度减 1，只有当所有模态全部关闭（深度归 0）才把滚动还给背景页。
 */
let scrollLockDepth = 0;
let originalBodyOverflow: string | null = null;

export function lockBodyScroll() {
  if (typeof document === 'undefined') return;
  if (scrollLockDepth === 0) {
    originalBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  scrollLockDepth += 1;
}

export function unlockBodyScroll() {
  if (typeof document === 'undefined') return;
  scrollLockDepth = Math.max(0, scrollLockDepth - 1);
  if (scrollLockDepth === 0) {
    document.body.style.overflow = originalBodyOverflow ?? '';
    originalBodyOverflow = null;
  }
}

/** 供测试读取当前锁定深度 */
export function getScrollLockDepth() {
  return scrollLockDepth;
}

export function useModalFocus(
  panel: Ref<HTMLElement | null>,
  onClose: () => void,
  options: {
    lockScroll?: boolean;
    /**
     * 打开时的初始焦点（批 69）。返回 null 则回落到面板容器。
     *
     * 默认落点是**面板容器**而非第一个可聚焦项：否则鼠标用户打开弹窗就会看到
     * 确认/关闭按钮上多出一圈蓝环，视觉上像"已经按下了什么"。
     * 但有一类弹窗的**唯一目的就是让用户输入**（危险操作的确认短语、管理员密码），
     * 此时把焦点直接放到该输入框既省一次 Tab，也让移动端键盘立刻弹出 ——
     * 由调用方通过本选项显式声明。
     */
    initialFocus?: () => HTMLElement | null | undefined;
  } = {},
) {
  const lockScroll = options.lockScroll !== false;
  const initialFocus = options.initialFocus;
  let lastFocused: Element | null = null;
  let token: symbol | null = null;

  function focusables(): HTMLElement[] {
    if (!panel.value) return [];
    return tabbableOf(Array.from(panel.value.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)));
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (stack[stack.length - 1] !== token) return;
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== 'Tab') return;
    const els = focusables();
    if (!els.length) return;
    // 焦点已不在面板里（例如刚点了面板内的纯文本）→ 拉回来，别漏到背景页
    if (!panel.value || !panel.value.contains(document.activeElement)) {
      e.preventDefault();
      (els[0] || panel.value)?.focus?.();
      return;
    }
    // 焦点停在面板容器上（打开时的默认落点）：正向 Tab 交给浏览器进第一个可聚焦项，
    // 反向 Tab 必须拦住，否则会退到遮罩后面的背景页
    if (document.activeElement === panel.value) {
      if (e.shiftKey) {
        e.preventDefault();
        els[els.length - 1].focus();
      }
      return;
    }
    const first = els[0];
    const last = els[els.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function attach() {
    token = Symbol('modal');
    stack.push(token);
    document.addEventListener('keydown', onKeydown, true);
  }

  function detach() {
    document.removeEventListener('keydown', onKeydown, true);
    if (token) {
      const i = stack.indexOf(token);
      if (i >= 0) stack.splice(i, 1);
      token = null;
    }
  }

  let isScrollLocked = false;

  function applyScrollLock() {
    if (lockScroll && !isScrollLocked) {
      lockBodyScroll();
      isScrollLocked = true;
    }
  }

  function removeScrollLock() {
    if (isScrollLocked) {
      unlockBodyScroll();
      isScrollLocked = false;
    }
  }

  /**
   * 跟随 `open` 状态调用：打开时记住原焦点、锁滚动、挂监听并把焦点落到 **`initialFocus()`
   * 指定的元素，未指定则落到面板容器**（`tabindex="-1"` + `outline-none`）；
   * 关闭时解锁、摘监听、把焦点还给触发元素。
   */
  async function sync(open: boolean) {
    if (open) {
      lastFocused = document.activeElement;
      applyScrollLock();
      await nextTick();
      attach();
      (initialFocus?.() || panel.value)?.focus?.();
      return;
    }
    removeScrollLock();
    detach();
    (lastFocused as HTMLElement | null)?.focus?.();
  }

  /** 组件卸载兜底：解开滚动锁与监听（避免留下"页面永远滚不动"）。 */
  function release() {
    removeScrollLock();
    detach();
  }

  return { sync, release };
}
