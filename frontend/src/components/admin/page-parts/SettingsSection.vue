<script setup lang="ts">
/**
 * SettingsSection · 设置区块外壳（**仅供 SecurityPage 使用**）
 * ---------------------------------------------------------------------------
 * 批 10 重构：改用全局 `.card` / `.card-head` 语汇（与其余管理页的分层卡片一致），
 * 不再自带一套 `rounded-xl + shadow-xs + 内联 surface 三元`。
 * 对外接口（props / slots）保持兼容：title · description · tone · actions / default。
 */
import type { Component } from 'vue'

defineProps<{
  title: string
  description?: string
  /** tone: 'default' 分层卡 | 'subtle' 内嵌底 */
  tone?: 'default' | 'subtle'
  /** 可选图标（重构新增；不传则不渲染，向后兼容） */
  icon?: Component
}>()
</script>

<template>
  <section class="card ss" :class="{ 'is-subtle': tone === 'subtle' }">
    <header class="card-head">
      <div class="ss-head-text">
        <!-- 批 45：h3 → h2。本组件承载的是**页面一级分区**，直接挂在布局 h1 之下，
             跳级（h1→h3）会让读屏器/大纲工具误判层级；其余管理页的卡片标题已是 h2。 -->
        <h2 class="card-title">
          <component :is="icon" v-if="icon" :size="14" />
          {{ title }}
        </h2>
        <p v-if="description" class="card-sub">{{ description }}</p>
      </div>
      <div class="ss-actions">
        <slot name="actions" />
      </div>
    </header>

    <div class="ss-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.ss {
  overflow: hidden;
}
.ss.is-subtle {
  background-color: var(--ds-color-bg-surface-inset);
}
.ss-head-text {
  min-width: 0;
}
.ss-actions {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.ss-body {
  padding: var(--ds-space-4);
}
</style>
