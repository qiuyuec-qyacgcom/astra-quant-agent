<script setup lang="ts">
/**
 * DangerZone.vue · 不可逆操作的危险区（共享 page-part）
 * ---------------------------------------------------------------------------
 * 契约：逐字保留 `title` / `description` / `confirmPhrase` / `actionLabel` /
 *      `placeholder` 五个 prop 与 `confirm` 事件；**逐字短语校验逻辑不变**
 *      （`typed === confirmPhrase` 才解锁按钮，确认后清空输入并重新上锁）。
 *      仅可在不可逆操作上使用；可逆操作请改用撤销 toast。
 *
 * 骨架（批 14 重写）：
 *   旧 = `repeating-linear-gradient` 斜纹警示条（**装饰性渐变，明令禁止**）
 *        + 6 处内联 `:style` 三元 + 自成一套的 `rounded-xl / font-black` 写法
 *   新 = 左侧危险色竖线（与 `.dg-stale` / `.sc-switch-row.is-danger` / `.is-error`
 *        同一套"左侧竖线表语义"的语汇）+ 共享 `.field` / `.btn` / `.form-label`
 *
 * 为什么去掉斜纹：本项目的危险语义一律靠**左侧 2px 竖线 + 语义色**表达，
 * 斜纹是另一套无关的视觉方言；且渐变属明令禁止的装饰手法。
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from '../../../composables/useI18n'
import { ShieldAlert, Lock, Unlock } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  title: string
  /** 究竟什么会被永久失去——一句直白的话 */
  description: string
  /** 用户必须逐字键入的资源名（如账号名、"ALL"、文件名） */
  confirmPhrase: string
  actionLabel: string
  placeholder?: string
}>(), { placeholder: '' })

const emit = defineEmits<{ (e: 'confirm'): void }>()
const { t } = useI18n()

const typed = ref('')
const unlocked = ref(false)
watch(() => props.confirmPhrase, () => { typed.value = ''; unlocked.value = false })

function onInput() {
  unlocked.value = typed.value === props.confirmPhrase
}
function act() {
  if (!unlocked.value) return
  emit('confirm')
  typed.value = ''
  unlocked.value = false
}

/** 提示语仍走既有键，保持与旧版逐字一致 */
const hint = computed(() => t('admin.shell.danger.confirmHint', undefined, { phrase: props.confirmPhrase }))
</script>

<template>
  <section class="dz" :class="{ 'is-armed': unlocked }">
    <header class="dz-head">
      <span class="icon-box dz-icon"><ShieldAlert :size="14" /></span>
      <div class="dz-head-text">
        <h3 class="dz-title">{{ title }}</h3>
        <p class="dz-desc">{{ description }}</p>
      </div>
    </header>

    <div class="dz-body">
      <label class="field-stack dz-field">
        <span class="form-label">{{ hint }}</span>
        <span class="dz-input-wrap">
          <Lock v-if="!unlocked" :size="12" class="dz-input-icon" />
          <Unlock v-else :size="12" class="dz-input-icon is-armed" />
          <input
            v-model="typed"
            type="text"
            autocomplete="off"
            spellcheck="false"
            :placeholder="placeholder || confirmPhrase"
            class="field dz-input mono"
            @input="onInput"
          />
        </span>
      </label>

      <button type="button"
        class="btn btn-danger btn-sm dz-action"
        :disabled="!unlocked"
        @click="act"
      >
        {{ actionLabel }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.dz {
  border: 1px solid var(--down-line);
  border-left: 2px solid var(--down);
  border-radius: var(--r-card);
  background-color: var(--ds-color-bg-surface-card);
  overflow: hidden;
}

/* 头部：危险色标题 + 直白的损失说明 */
.dz-head {
  display: flex;
  align-items: flex-start;
  gap:10px;
  padding: var(--ds-space-4);
  background-color: var(--down-bg);
  border-bottom: 1px solid var(--down-line);
}
/* 危险语义 delta：几何与配色基座来自全站 .icon-box 原件（styles/components.css） */
.dz-icon {
  background-color: var(--down-bg);
  color: var(--down);
}
.dz-head-text {
  display: flex;
  flex-direction: column;
  gap:4px;
  min-width: 0;
}
.dz-title {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--track-label);
  color: var(--down);
}
.dz-desc {
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}

/* 主体：短语输入 + 危险动作 */
.dz-body {
  display: flex;
  align-items: flex-end;
  gap: var(--ds-space-3);
  padding: var(--ds-space-4);
}
@media (max-width: 640px) {
  .dz-body {
    flex-direction: column;
    align-items: stretch;
  }
}

/* 字段栈 delta：竖排 + 6px + min-width 来自全站 .field-stack 原件 */
.dz-field {
  flex: 1;
}
.dz-input-wrap {
  position: relative;
  display: block;
}
.dz-input-icon {
  position: absolute;
  left: 9px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--ds-color-text-placeholder);
  pointer-events: none;
}
.dz-input-icon.is-armed {
  color: var(--down);
}
/* 批 97：27px = 左侧 `.dz-input-icon`（absolute left 9）+ 图标宽 12 + 间隙 6
   —— **推导几何**，不是间距口味。实测图标占 x 9~21、文字从 28 起，不重叠。
   此值随图标尺寸走，故刻意留在 2px 刻度之外（spacing 判据里已登记）。 */
.dz-input {
  width: 100%;
  padding-left: 27px;
}
/* 解锁后输入框转危险色描边，明确告知"现在可以按了" */
.dz.is-armed .dz-input {
  border-color: var(--down);
}
.dz-action {
  flex-shrink: 0;
}
</style>
