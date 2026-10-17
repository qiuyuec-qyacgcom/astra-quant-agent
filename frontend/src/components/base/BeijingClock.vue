<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { fmtDateTime } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';
const { t } = useI18n();
const now = ref(new Date());
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => { timer = setInterval(() => { now.value = new Date(); }, 1000); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <time
    class="dsh-pill font-mono whitespace-nowrap text-3xs"
    :datetime="now.toISOString()"
    :title="t('time.siteTimeTip')"
  >
    <span class="text-[var(--ink-2)]">{{ fmtDateTime(now) }}</span>
    <span class="text-[var(--ink-3)] font-semibold">BJT</span>
  </time>
</template>
