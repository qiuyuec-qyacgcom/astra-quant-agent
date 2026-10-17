<script setup lang="ts">
/** 相对时间显示（走 i18n），30s 心跳刷新；绝对时间进 title */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { fmtDateTime, parseTime } from '../../utils/format';
import { useI18n } from '../../composables/useI18n';

/** 批A(2026-09-13)·共享心跳：旧实现每个实例各起一个 30s interval——快讯列表实测
 *  35 行即 35 个常驻定时器（+随条数线性增长，切走仍在跑），移动端纯浪费。改为
 *  模块级单定时器 + 引用计数，最后一个实例卸载时才停表；对外 API 不变。 */
const _subs = new Set<() => void>();
let _shared: number | undefined;
function subscribe(cb: () => void): () => void {
  _subs.add(cb);
  if (_shared === undefined) _shared = window.setInterval(() => { for (const f of _subs) f(); }, 30_000);
  return () => {
    _subs.delete(cb);
    if (!_subs.size && _shared !== undefined) {
      window.clearInterval(_shared);
      _shared = undefined;
    }
  };
}

const props = defineProps<{ time: string | number | Date }>();
const { t } = useI18n();
const now = ref(Date.now());
let unsubscribe: (() => void) | null = null;

onMounted(() => {
  unsubscribe = subscribe(() => (now.value = Date.now()));
});
onBeforeUnmount(() => {
  unsubscribe?.();
  unsubscribe = null;
});

const text = computed(() => {
  const ts = parseTime(props.time);
  const diff = Math.max(0, now.value - ts.getTime());
  if (Number.isNaN(ts.getTime())) return String(props.time);
  const s = Math.floor(diff / 1000);
  if (s < 5) return t('time.justNow');
  if (s < 60) return t('time.secondsAgo', undefined, { n: s });
  const m = Math.floor(s / 60);
  if (m < 60) return t('time.minutesAgo', undefined, { n: m });
  const h = Math.floor(m / 60);
  if (h < 24) return t('time.hoursAgo', undefined, { n: h });
  return t('time.daysAgo', undefined, { n: Math.floor(h / 24) });
});
const abs = computed(() => `${fmtDateTime(props.time)} ${t('time.beijingTime')}`);
const iso = computed(() => {
  const ts = parseTime(props.time);
  return !Number.isNaN(ts.getTime()) ? ts.toISOString() : undefined;
});
</script>

<template>
  <time class="t-faint whitespace-nowrap" :datetime="iso" :title="abs">{{ text }}</time>
</template>
