<script setup lang="ts">
/**
 * DataGate.vue · 行情侧数据门（加载 / 报错 / 陈旧 三态统一出口）
 * ---------------------------------------------------------------------------
 * ## 为什么需要它
 *
 * `stores/dashboard.ts` 早就暴露了 `loading` / `error` / `isConnected`，
 * 但推倒重来期审计发现：**行情侧 5 个数据页没有一个读取这三个状态**，
 * 一律只读 `store.data`。
 *
 * 后果是一个语义谎报：后端不可用时 `store.error` 被赋值、`isConnected=false`，
 * 而各页计算 `store.data?.trades || []` 得到空数组 → 渲染「暂无记录」。
 * 即**「加载失败」被显示成「本来就没有数据」** —— 用户看不到任何异常，
 * 也没有重试入口，只会以为这个页面本来就是空的。
 *
 * 本件把三态判断收成**唯一一处**，各页只需把内容塞进默认插槽。
 *
 * ## 三态判定（顺序即优先级）
 *
 * 1. `failed`   —— 从未拿到过数据 **且** 不在加载中 **且** 有错误 → 报错 + 重试；
 * 2. `firstLoad`—— 从未拿到过数据且未判定失败（含首个 tick，此时 loading 可能尚未置位）→ 骨架；
 * 3. 其余      —— 有数据：渲染内容；若此刻 `isConnected=false` 或仍有 error，
 *                在内容**上方**加一条不阻断操作的陈旧提示条。
 *
 * 第 3 条是本件的关键取舍：**轮询失败不得把已有数据抹掉**。
 * 后端掉线时页面应继续展示最后一次成功快照（并明确标注其时间与"可能已过时"），
 * 而不是整页变成错误页 —— 行情工位在断线时仍然要能看。
 */
import { computed } from 'vue'
import { AlertTriangle, RefreshCw, Loader2, WifiOff } from 'lucide-vue-next'
import { useDashboardStore } from '../../stores/dashboard'
import { useI18n } from '../../composables/useI18n'

const store = useDashboardStore()
const { t } = useI18n()

/** 是否已经拿到过至少一次快照 */
const hasData = computed(() => store.data !== null)

/** 从未成功、且已明确失败 → 报错态 */
const failed = computed(() => !hasData.value && !store.loading && !!store.error)

/** 从未成功、但还不能判定失败（含首个 tick）→ 骨架态 */
const firstLoad = computed(() => !hasData.value && !failed.value)

/** 有数据、但链路已不可信 → 内容上方补一条陈旧提示（不阻断） */
const degraded = computed(() => hasData.value && (!store.isConnected || !!store.error))

const staleAt = computed(() => {
  const d = store.lastUpdated
  if (!d) return '--'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
})

/** 重试：直接触发一次非静默拉取（与布局层的轮询互为补充，不改动其定时器） */
async function retry() {
  await store.fetchDashboard(false)
}
</script>

<template>
  <!-- ① 首次加载：骨架 -->
  <div v-if="firstLoad" class="dg" aria-busy="true">
    <p class="dg-loading">
      <Loader2 :size="13" class="animate-spin shrink-0" />
      <span>{{ t('dash.shell.gateLoading') }}</span>
    </p>
    <div class="dg-skel">
      <div v-for="i in 4" :key="i" class="skeleton dg-skel-card" />
    </div>
  </div>

  <!-- ② 从未加载成功：报错 + 重试 -->
  <div v-else-if="failed" role="alert" class="state-block is-error">
    <span class="state-icon"><AlertTriangle :size="17" /></span>
    <p class="state-title">{{ t('dash.shell.gateFailedTitle') }}</p>
    <p class="state-desc">{{ t('dash.shell.gateFailedDesc') }}</p>
    <p class="dg-error-raw mono">{{ store.error }}</p>
    <button type="button" class="btn btn-ghost btn-sm" :disabled="store.loading" @click="retry">
      <Loader2 v-if="store.loading" :size="14" class="animate-spin shrink-0" />
      <RefreshCw v-else :size="14" />
      <span>{{ store.loading ? t('dash.shell.gateRetrying') : t('dash.shell.gateRetry') }}</span>
    </button>
  </div>

  <!-- ③ 已加载：内容（链路不可信时上方补陈旧条，但绝不隐藏内容） -->
  <template v-else>
    <div v-if="degraded" class="dg-stale">
      <WifiOff :size="13" />
      <span class="dg-stale-body">
        <b>{{ t('dash.shell.gateStaleTitle') }}</b>
        <span>{{ t('dash.shell.gateStaleDesc', undefined, { t: staleAt }) }}</span>
      </span>
      <button type="button" class="btn btn-quiet btn-sm" :disabled="store.loading" @click="retry">
        <Loader2 v-if="store.loading" :size="14" class="animate-spin shrink-0" />
        <RefreshCw v-else :size="14" />
        <span>{{ store.loading ? t('dash.shell.gateRetrying') : t('dash.shell.gateRetry') }}</span>
      </button>
    </div>

    <slot />
  </template>
</template>

<style scoped>
.dg {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
}

/* —— 骨架 —— */
.dg-loading {
  display: flex;
  align-items: center;
  gap:8px;
  padding:16px 16px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-card);
  background-color: var(--surface-2);
  font-size: var(--text-3xs);
  color: var(--ink-2);
}
.dg-skel {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 900px) {
  .dg-skel {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.dg-skel-card {
  height: 132px;
  border-radius: var(--r-card);
}

/* —— 报错 —— */
.dg-error-raw {
  max-width: 640px;
  font-size: var(--text-4xs);
  line-height: var(--leading-body);
  color: var(--ink-3);
  overflow-wrap: anywhere;
}

/* —— 陈旧提示条（不阻断）—— */
.dg-stale {
  display: flex;
  align-items: center;
  gap:10px;
  padding:10px 16px;
  border: 1px solid var(--warn-line);
  border-left: 2px solid var(--warn);
  border-radius: var(--r-card);
  background-color: var(--warn-bg);
  color: var(--warn);
  font-size: var(--text-3xs);
}
.dg-stale > svg {
  flex-shrink: 0;
}
.dg-stale-body {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.dg-stale-body b {
  font-weight: 600;
}
.dg-stale-body span {
  color: var(--ink-2);
}
.dg-stale > button {
  margin-left: auto;
  flex-shrink: 0;
}
</style>
