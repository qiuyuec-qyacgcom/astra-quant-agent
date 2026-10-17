import { ref, type Ref } from 'vue';

/**
 * useRovingTabs · WAI-ARIA APG 选项卡/分段条的键盘交互单一事实源
 * ---------------------------------------------------------------------------
 * 批 66：全站有 9 个页面各自手写 `role="tab"` 按钮组，共 19 个按钮，全部存在同样的两个缺陷：
 *
 *   1. **没有漫游 tabindex**：`role="tab"` 的元素默认全部落在 Tab 键顺序里，
 *      键盘用户要按 N 次 Tab 才能穿过一条筛选分段条，WCAG 2.1.1（键盘）实质不达标；
 *   2. **方向键无响应**：APG 明确要求 ←/→ 在 tablist 内移动焦点，Home/End 跳首尾。
 *
 * 该逻辑此前被复制粘贴多次，修一处漏八处。此文件把它收敛成唯一实现，
 * 所有 tablist（含 BaseTabs / BaseSegmented 两个基准件）都必须经由它绑定键盘行为。
 *
 * 用法：
 * ```ts
 * const { setRef, onKeydown, roving } = useRovingTabs(
 *   () => options.value.length,
 *   (i) => { model.value = options.value[i].value },
 * );
 * ```
 * ```html
 * <button
 *   v-for="(opt, i) in options"
 *   :ref="setRef(i)"
 *   role="tab"
 *   :tabindex="roving(model === opt.value)"
 *   @keydown="onKeydown($event, i)"
 * />
 * ```
 */
export function useRovingTabs(count: () => number, activate: (index: number) => void) {
  const refs = ref<HTMLButtonElement[]>([]) as Ref<HTMLButtonElement[]>;

  /** v-for 里要用的函数式 ref：只在挂载时写入，卸载的 undefined 不覆盖已有节点。 */
  function setRef(index: number) {
    return (el: unknown) => {
      if (el) refs.value[index] = el as HTMLButtonElement;
    };
  }

  function focusAt(index: number) {
    refs.value[index]?.focus();
  }

  /**
   * 漫游 tabindex：仅当前选中项返回 0，其余 -1。
   * 这样整条 tablist 在 Tab 键顺序里只占一格，组内导航交给方向键。
   */
  function roving(selected: boolean) {
    return selected ? 0 : -1;
  }

  /**
   * 方向键处理。←/→ 与 ↑/↓ 均支持（分段条横竖两种排布都有），环绕不卡边界，
   * Home/End 直达首尾；命中后阻止滚动等默认行为，并把焦点与激活态一起搬过去。
   */
  function onKeydown(e: KeyboardEvent, index: number) {
    const n = count();
    if (!n) return;

    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (index + 1) % n;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (index - 1 + n) % n;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    if (next < 0) return;

    e.preventDefault();
    activate(next);
    // 焦点必须在 DOM 更新后重新落到新选中项上；Vue 的 tabindex 变更不自动转移焦点。
    focusAt(next);
  }

  return { setRef, onKeydown, focusAt, roving };
}
