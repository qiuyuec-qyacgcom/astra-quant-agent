<script setup lang="ts">
/** 置信档位：未校准概率不展示裸数字 —— 高/中/低 + 色点，原值进 title */
import { computed } from 'vue';
import { confTier } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ value: number | null | undefined }>();
const { t } = useI18n();

const tier = computed(() => confTier(props.value));
const dot = computed(() => {
  const v = tier.value?.tier;
  return v === 'high' ? 'var(--up)' : v === 'mid' ? 'var(--warn)' : 'var(--ink-3)';
});
const ariaLabel = computed(() => {
  if (!tier.value) return '';
  const num = Math.round((Number(props.value) || 0) * 100) / 100;
  return `${t(`common.conf.${tier.value.tier}`)} (${num})`;
});
</script>

<template>
  <span
    v-if="tier"
    class="badge"
    :title="`${Math.round((Number(value) || 0) * 100) / 100}`"
    :aria-label="ariaLabel"
  >
    <span class="dot" :style="{ backgroundColor: dot }" aria-hidden="true" />
    {{ t(`common.conf.${tier.tier}`) }}
  </span>
  <span v-else class="t-faint" aria-label="--">--</span>
</template>
