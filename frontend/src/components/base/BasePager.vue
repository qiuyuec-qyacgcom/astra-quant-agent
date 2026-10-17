<script setup lang="ts">
/** 轻量分页：上一页/下一页 + 页码摘要 */
import { computed } from 'vue';
import { ChevronLeft, ChevronRight } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ page: number; pageCount: number; total: number }>();
const emit = defineEmits<{ (e: 'update:page', v: number): void }>();
const { t } = useI18n();

const info = computed(() =>
  props.pageCount <= 0
    ? t('common.noRecords')
    : t('common.pageInfo', undefined, { page: props.page, pages: props.pageCount, total: props.total }),
);
</script>

<template>
  <nav class="flex items-center justify-between gap-3 px-3.5 py-2" :aria-label="t('common.pageNav')">
    <span class="t-faint num text-xs" role="status" aria-live="polite">{{ info }}</span>
    <div v-if="pageCount > 1" class="flex items-center gap-1">
      <button type="button"
        class="btn btn-ghost btn-icon btn-sm"
        :disabled="page <= 1"
        :title="t('common.prevPage')"
        :aria-label="t('common.prevPage')"
        @click="emit('update:page', page - 1)"
      >
        <ChevronLeft />
      </button>
      <button type="button"
        class="btn btn-ghost btn-icon btn-sm"
        :disabled="page >= pageCount"
        :title="t('common.nextPage')"
        :aria-label="t('common.nextPage')"
        @click="emit('update:page', page + 1)"
      >
        <ChevronRight />
      </button>
    </div>
  </nav>
</template>
