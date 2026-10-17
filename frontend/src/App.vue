<script setup lang="ts">
import { onMounted } from 'vue'
import { useTheme } from './composables/useTheme'
import { useI18n } from './composables/useI18n'
import ToastHost from './components/base/ToastHost.vue'
import ConfirmHost from './components/base/ConfirmHost.vue'
import PeekDrawer from './components/dashboard/PeekDrawer.vue'
import ErrorBoundary from './components/base/ErrorBoundary.vue'

const { initTheme } = useTheme()
const { initLocale } = useI18n()

onMounted(() => {
  initTheme()
  initLocale()
})
</script>

<template>
  <ErrorBoundary>
    <router-view />
  </ErrorBoundary>
  <ToastHost />
  <ConfirmHost />
  <PeekDrawer />
</template>
