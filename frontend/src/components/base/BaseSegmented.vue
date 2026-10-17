<script setup lang="ts" generic="T extends string | number">
/**
 * 分段选择器：v-model + options（value/label/icon/title）
 * ---------------------------------------------------------------------------
 * 批 66：与 BaseTabs 同步补齐 WAI-ARIA APG 键盘契约。
 *   `role="tab"` 的元素若不在漫游 tabindex 里，键盘用户会在同一组内被迫按 N 次 Tab，
 *   且方向键无任何反馈。现在仅选中项 tabindex="0"，←/→/↑/↓ 环绕、Home/End 跳首尾。
 */
import { type Component } from 'vue';
import { useRovingTabs } from '../../composables/useRovingTabs';

const props = defineProps<{
  modelValue: T;
  options: { value: T; label?: string; icon?: Component; title?: string }[];
  large?: boolean;
  /**
   * 组名（批 44）：`role="tablist"` 没有可访问名时读屏器只会念"标签列表"，
   *  用户不知道这三个分段在筛选什么（状态/方向/结果）。
   *
   *  批 110：由可选改为**必填**。实测 25 路由 17 条 tablist 里有 **4 条没有名字**
   *  （周期分段、持仓/挂单分段，`/` 与 `/trading` 各两条）—— 都是漏传该 prop。
   *  改成必填后，漏传会在 `vue-tsc` 阶段直接失败，不必再等人肉发现。
   */
  label: string;
}>();

const emit = defineEmits<{ (e: 'update:modelValue', v: T): void }>();

// ↑/↓ 与 ←/→ 等价：分段条既有横向也有纵向排布，两轴都不应无响应。
const { setRef, onKeydown, roving } = useRovingTabs(
  () => props.options.length,
  (i) => emit('update:modelValue', props.options[i].value),
);
</script>

<template>
  <div class="seg" :class="large && 'seg-lg'" role="tablist" :aria-label="label">
    <button
      v-for="(opt, idx) in options"
      :key="String(opt.value)"
      :ref="setRef(idx)"
      type="button"
      role="tab"
      :aria-selected="modelValue === opt.value"
      :tabindex="roving(modelValue === opt.value)"
      :class="{ 'seg-on': modelValue === opt.value }"
      :title="opt.title"
      @click="$emit('update:modelValue', opt.value)"
      @keydown="onKeydown($event, idx)"
    >
      <component v-if="opt.icon" :is="opt.icon" class="h-3.5 w-3.5" aria-hidden="true" />
      <span v-if="opt.label">{{ opt.label }}</span>
    </button>
  </div>
</template>
