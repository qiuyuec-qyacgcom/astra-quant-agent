<script setup lang="ts">
/** 偏好弹层：主题 / 语言 / 色盲配色 —— 收进一个 ⚙，顶栏不再散落按钮 */
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue';
import { SlidersHorizontal, BookOpen, LayoutDashboard, Eye } from 'lucide-vue-next';
import { useRouter } from 'vue-router';
import { useUi } from '../../composables/useUi';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import { usePopoverFocus } from '../../composables/usePopoverFocus';
import BaseSegmented from '../base/BaseSegmented.vue';
import BaseSwitch from '../base/BaseSwitch.vue';

const { t, currentLocale, setLocale, LOCALE_OPTIONS } = useI18n();
const { theme, setTheme, cvd, toggleCvd } = useTheme();

const THEME_OPTIONS = computed(() => [
  { value: 'light', label: t('dash.shell.settings.themeLight') },
  { value: 'dark', label: t('dash.shell.settings.themeDark') },
]);

const router = useRouter();
const { peekOpen } = useUi();
const open = ref(false);
const trigger = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const panelId = useId();

/* 批 104：这是个**非模态**气泡（无 aria-modal、无遮罩、允许点背景），
   所以不用 useModalFocus（那会顺带锁滚动 + 困住 Tab），只要焦点交接。 */
const { release: releasePopoverFocus } = usePopoverFocus(panel, trigger, open);

function onDocDown(e: MouseEvent) {
  const el = e.target as Node;
  // 守卫同时检查触发器与面板，避免 capture 阶段先关后丢 handler（P1 教训）
  if (open.value && !trigger.value?.contains(el) && !panel.value?.contains(el)) open.value = false;
}
function onEsc(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false;
}
onMounted(() => {
  document.addEventListener('mousedown', onDocDown);
  window.addEventListener('keydown', onEsc);
});
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown);
  window.removeEventListener('keydown', onEsc);
  releasePopoverFocus();
});
</script>

<template>
  <div class="relative">
    <button type="button"
      ref="trigger"
      class="btn btn-quiet btn-icon"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-controls="open ? panelId : undefined"
      :aria-label="t('dash.shell.settings.title')"
      :title="t('dash.shell.settings.title')"
      @click="open = !open"
    >
      <SlidersHorizontal class="h-4 w-4" />
    </button>
    <Transition name="pop">
      <!-- 批B(2026-09-13)：窄屏防溢出——固定 256px 且无上限高时，小屏横向可能被裁、
           竖屏矮窗内容够不着。限宽到视口 -24px，并给纵向滚动（对齐 ToastHost 既有约定）。 -->
      <div
        v-if="open"
        :id="panelId"
        ref="panel"
        tabindex="-1"
        class="float-panel absolute end-0 top-10 w-64 max-w-[calc(100vw-24px)] max-h-[70vh] overflow-y-auto p-3 outline-none"
        role="dialog"
        :aria-label="t('dash.shell.settings.title')"
      >
        <div class="space-y-3">
          <div>
            <p class="form-label mb-1.5">{{ t('dash.shell.settings.theme') }}</p>
            <BaseSegmented
              class="w-full"
              :label="t('dash.shell.settings.theme')"
              :model-value="theme"
              :options="THEME_OPTIONS"
              @update:model-value="(v: any) => setTheme(v)"
            />
          </div>
          <div>
            <p class="form-label mb-1.5">{{ t('dash.shell.settings.language') }}</p>
            <BaseSegmented
              class="w-full"
              :label="t('dash.shell.settings.language')"
              :model-value="currentLocale"
              :options="LOCALE_OPTIONS"
              @update:model-value="(v: any) => setLocale(v)"
            />
          </div>
          <label class="flex cursor-pointer items-center justify-between gap-3 py-0.5">
            <span>
              <span class="block text-sm font-medium" style="color: var(--ink-1)">{{ t('dash.shell.settings.cvd') }}</span>
              <span class="block text-xs leading-snug" style="color: var(--ink-3)">{{ t('dash.shell.settings.cvdDesc') }}</span>
            </span>
            <BaseSwitch :model-value="cvd" :label="t('dash.shell.settings.cvd')" @update:model-value="toggleCvd()" />
          </label>
          <div class="border-t pt-2" style="border-color: var(--line-1)">
            <p class="form-label mb-1">{{ t('dash.shell.settings.goto') }}</p>
            <button type="button" class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-[var(--surface-3)]" style="color: var(--ink-1)" @click="open = false; router.push('/docs')">
              <BookOpen class="h-4 w-4" style="color: var(--ink-3)" />{{ t('nav.actions.docs') }}
            </button>
            <button type="button" class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-[var(--surface-3)]" style="color: var(--ink-1)" @click="open = false; router.push('/admin')">
              <LayoutDashboard class="h-4 w-4" style="color: var(--ink-3)" />{{ t('nav.actions.console') }}
            </button>
            <button type="button" class="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-[var(--surface-3)]" style="color: var(--ink-1)" @click="open = false; peekOpen = true">
              <Eye class="h-4 w-4" style="color: var(--ink-3)" />{{ t('nav.actions.promptPeek') }}
            </button>
          </div>
          <p class="t-faint border-t pt-2 text-xs" style="border-color: var(--line-1)">
            {{ t('dash.shell.settings.dataNote') }}
          </p>
        </div>
      </div>
    </Transition>
  </div>
</template>
