<script setup lang="ts">
/** 代码块：滚动 + 复制角标 */
import CopyButton from './CopyButton.vue';
withDefaults(defineProps<{ code: string; lang?: string; maxHeight?: string }>(), { maxHeight: '320px' });
</script>

<template>
  <div class="relative group">
    <pre class="code-block" tabindex="0" :style="{ maxHeight }">{{ code }}</pre>
    <!-- 触屏无 hover，复制钮必须常驻（批A：hover:none 兜底 + 键盘 focus-within，对齐 .row-actions 既有约定） -->
    <div class="absolute right-1.5 top-1.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
      <CopyButton :text="code" />
    </div>
  </div>
</template>
