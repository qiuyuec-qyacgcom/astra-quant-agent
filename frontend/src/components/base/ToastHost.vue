<script setup lang="ts">
/** Toast 渲染宿主：右上角堆叠，App.vue 挂一次。 */
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from 'lucide-vue-next';
import { ref } from 'vue';
import { useToast } from '../../composables/useToast';
import { useI18n } from '../../composables/useI18n';

const { items, dismiss, pause, resume } = useToast();
const { t } = useI18n();

/* 批 108：鼠标移入 / 键盘焦点进入提示区就暂停倒计时，离开按剩余时间继续 ——
   长消息（尤其带第二行建议文案的错误）不至于读一半就消失。
   focusout 会在**内部**两个可聚焦元素之间跳转时也触发，故只在焦点真的
   离开整个提示区（relatedTarget 不在容器内）时才恢复。 */
const stack = ref<HTMLElement | null>(null);

function onFocusOut(e: FocusEvent) {
  const next = e.relatedTarget as Node | null;
  if (!next || !stack.value || !stack.value.contains(next)) resume();
}

const icons = { ok: CheckCircle2, err: XCircle, warn: AlertTriangle, info: Info } as const;
const colors = {
  ok: 'var(--up)',
  err: 'var(--down)',
  warn: 'var(--warn)',
  info: 'var(--info)',
} as const;
</script>

<template>
  <Teleport to="body">
    <div
      ref="stack"
      class="fixed right-3 top-14 flex w-[340px] max-w-[calc(100vw-24px)] flex-col gap-2"
      style="z-index: var(--z-toast)"
      aria-live="polite"
      @mouseenter="pause"
      @mouseleave="resume"
      @focusin="pause"
      @focusout="onFocusOut"
    >
      <TransitionGroup name="toast">
        <div
          v-for="item in items"
          :key="item.id"
          class="float-panel flex items-start gap-2.5 px-3.5 py-3"
          style="border-radius: var(--r-card)"
          :role="item.kind === 'err' ? 'alert' : 'status'"
        >
          <component :is="icons[item.kind]" class="mt-px h-4 w-4 shrink-0" :style="{ color: colors[item.kind] }" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold leading-snug" style="color: var(--ink-1)">{{ item.title }}</p>
            <p v-if="item.desc" class="mt-0.5 text-xs leading-body" style="color: var(--ink-2)">{{ item.desc }}</p>
          </div>
          <button type="button"
            class="-me-1 mt-px rounded p-1 opacity-60 transition-opacity hover:opacity-100 focus:opacity-100 cursor-pointer"
            :title="t('common.close')"
            :aria-label="t('common.close')"
            @click="dismiss(item.id)"
          >
            <X class="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
/* 批 93：过渡时长/缓动一律取令牌（--dur-fast/base/slow、--ease-out）。
   全站 59 条 transition 里 55 条本就用令牌，只有 ToastHost 与 TrajectoryPanel
   两个文件写了刻度外的字面值（0.18 / 0.2 / 0.22 / 0.24s 与裸 ease）——
   同一个 toast 的进 / 出 / 移动竟用三个不同时长。已收敛。 */
.toast-enter-active { transition: transform var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out); }
.toast-leave-active { transition: all var(--dur-base) var(--ease-out); position: absolute; width: 100%; }
.toast-enter-from { transform: translateX(24px); opacity: 0; }
.toast-leave-to { transform: translateX(16px); opacity: 0; }
.toast-move { transition: transform var(--dur-base) var(--ease-out); }
</style>
