<script setup lang="ts">
/** 全局确认框宿主：由 useConfirm().ask() 驱动，App.vue 挂一次。 */
import { computed, ref, watch } from 'vue';
import { AlertTriangle } from 'lucide-vue-next';
import BaseDialog from './BaseDialog.vue';
import { useConfirm } from '../../composables/useConfirm';
import { useI18n } from '../../composables/useI18n';

const { state, settle } = useConfirm();
const { t } = useI18n();

const phraseInput = ref('');
const phraseOk = computed(() => !state.value.confirmPhrase || phraseInput.value.trim() === state.value.confirmPhrase);

watch(
  () => [state.value.open, state.value.id],
  () => {
    phraseInput.value = '';
  },
);

function onConfirm() {
  if (!phraseOk.value) return;
  settle(true);
}
</script>

<template>
  <BaseDialog
    :open="state.open"
    :title="state.title"
    :desc="state.desc"
    :size="'sm'"
    :tone="state.danger ? 'danger' : 'default'"
    :show-close="false"
    initial-focus="input"
    @close="settle(false)"
  >
    <template #title>
      <span class="flex items-center gap-2">
        <span
          v-if="state.danger"
          class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
          style="background-color: var(--down-bg); border: 1px solid var(--down-line)"
        >
          <AlertTriangle class="h-3 w-3" style="color: var(--down)" />
        </span>
        <span>{{ state.title }}</span>
      </span>
    </template>

    <div v-if="state.detail" class="mb-3">
      <p class="mono break-all text-xs" style="color: var(--ink-3)">{{ state.detail }}</p>
    </div>

    <div v-if="state.confirmPhrase" class="mt-2">
      <label class="form-label">
        {{ t('common.confirmPhraseHint', undefined, { phrase: state.confirmPhrase }) }}
      </label>
      <input
        v-model="phraseInput"
        type="text"
        autocomplete="off"
        spellcheck="false"
        class="field mono"
        :aria-label="t('common.confirmPhraseHint', undefined, { phrase: state.confirmPhrase })"
        :aria-invalid="phraseInput.length > 0 && !phraseOk"
        :placeholder="state.confirmPhrase"
        @keyup.enter="onConfirm"
      />
    </div>

    <template #footer>
      <button type="button" class="btn btn-ghost" @click="settle(false)">{{ state.cancelText || t('common.cancel') }}</button>
      <button
        type="button"
        class="btn"
        :class="state.danger ? 'btn-danger' : 'btn-primary'"
        :disabled="!phraseOk"
        @click="onConfirm"
      >
        {{ state.okText || t('common.confirm') }}
      </button>
    </template>
  </BaseDialog>
</template>
