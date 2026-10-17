<script setup lang="ts">
/**
 * BaseEmpty.vue · 空状态基准件
 * ---------------------------------------------------------------------------
 * 统一走 .state-block 三态骨架（加载 / 空 / 报错同骨架，见 components.css 第 10 节）。
 * 修正：重构前 .empty 类在样式表中并不存在，空态一直是无样式的裸文本块。
 * 契约：props（text / desc / icon）与 action 插槽保持向后兼容。
 */
import { Inbox } from 'lucide-vue-next';
import type { Component } from 'vue';

withDefaults(defineProps<{ text: string; desc?: string; icon?: Component }>(), {});
</script>

<template>
  <div class="state-block">
    <span class="state-icon">
      <component :is="icon || Inbox" :size="17" />
    </span>
    <p class="state-title">{{ text }}</p>
    <p v-if="desc" class="state-desc">{{ desc }}</p>
    <div v-if="$slots.action" class="state-action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.state-action {
  margin-top: var(--ds-space-1);
}
</style>
