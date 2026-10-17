<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { RefreshCw, LayoutDashboard, AlertTriangle } from 'lucide-vue-next'

const { t } = useI18n()
const hasError = ref(false)
const errorMessage = ref('')

onErrorCaptured((err: unknown) => {
  console.error('[ErrorBoundary caught]', err)
  hasError.value = true
  errorMessage.value = err instanceof Error ? err.message : String(err)
  return false
})

function reload() {
  try {
    sessionStorage.clear()
  } catch {
    // ignore
  }
  const url = new URL(window.location.href)
  url.searchParams.set('_v', String(Date.now()))
  window.location.href = url.toString()
}

function goHome() {
  try {
    sessionStorage.clear()
  } catch {
    // ignore
  }
  window.location.href = `/?_v=${Date.now()}`
}
</script>

<template>
  <div
    v-if="hasError"
    role="alert"
    class="min-h-screen w-full flex items-center justify-center p-4"
    style="background-color: var(--surface-0); color: var(--ink-1)"
  >
    <div class="dsh-card max-w-md w-full p-6 text-center space-y-4">
      <span class="mx-auto flex h-10 w-10 items-center justify-center rounded-full" style="background-color: var(--warn-bg); color: var(--warn)">
        <AlertTriangle class="h-5 w-5" aria-hidden="true" />
      </span>
      <div class="space-y-1.5">
        <h1 class="text-sm font-bold text-[var(--ink-strong)]">
          {{ t('common.errorBoundary.title') }}
        </h1>
        <p class="text-xs text-[var(--ink-2)] leading-relaxed">
          {{ t('common.errorBoundary.desc') }}
        </p>
        <p v-if="errorMessage" role="status" aria-live="polite" class="text-3xs font-mono text-[var(--ink-3)] break-all max-h-24 overflow-y-auto pt-1">
          {{ errorMessage }}
        </p>
      </div>
      <div class="flex items-center justify-center gap-2 pt-2">
        <button
          type="button"
          class="btn btn-primary h-8 px-3 text-xs font-medium cursor-pointer inline-flex items-center gap-1.5"
          @click="reload"
        >
          <RefreshCw class="h-3.5 w-3.5" aria-hidden="true" />
          <span>{{ t('common.errorBoundary.reload') }}</span>
        </button>
        <button
          type="button"
          class="btn btn-ghost h-8 px-3 text-xs font-medium cursor-pointer inline-flex items-center gap-1.5 border border-[var(--line-1)]"
          @click="goHome"
        >
          <LayoutDashboard class="h-3.5 w-3.5" aria-hidden="true" />
          <span>{{ t('common.errorBoundary.backHome') }}</span>
        </button>
      </div>
    </div>
  </div>
  <slot v-else />
</template>
