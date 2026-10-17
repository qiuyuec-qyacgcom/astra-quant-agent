<script setup lang="ts">
/** 方向标签：多/空/观望 —— 永远带 ▲▼ 符号，不依赖颜色语义 */
import { computed } from 'vue';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ dir: 'long' | 'short' | 'flat' | string; size?: 'sm' | 'md' }>();
const { t } = useI18n();

const norm = computed<'long' | 'short' | 'flat'>(() => {
  const d = String(props.dir || '').toUpperCase();
  // 批A(2026-09-13)：台账源头值是中文「多/空」(sync_full_ledger)，此前不识别→flat，
  // 迫使各调用点自写 `=== '多' ? 'long' : 'short'`，而该三元把 'long'/'buy'/空串
  // 一律误压成 short（方向标签整体反向）。中文档位在此统一识别，消费端可直传原值。
  if (d === '多' || d.includes('LONG') || d === 'BUY' || d === 'B') return 'long';
  if (d === '空' || d.includes('SHORT') || d === 'SELL' || d === 'S') return 'short';
  return 'flat';
});
const glyph = computed(() => ({ long: '▲', short: '▼', flat: '—' })[norm.value]);
const label = computed(() => t(`common.dir.${norm.value}`));
</script>

<template>
  <span class="dir" :class="`dir-${norm}`">
    <span aria-hidden="true">{{ glyph }}</span>{{ label }}
  </span>
</template>
