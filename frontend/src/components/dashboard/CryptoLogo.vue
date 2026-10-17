<script setup lang="ts">
/**
 * 币种真实头像组件（CryptoLogo.vue）
 * ---------------------------------------------------------------------------
 * 极简高可用四层降级：
 *   1. 本地官方高清真实代币图（经 Vite /assets 打包并持久缓存，含真实柴犬 DOGE、真实 PEPE、BTC、ETH 等）
 *   2. 内置官方超清矢量 SVG（本地图未命中或加载异常时秒级承接，绝不掉到单字母）
 *   3. 公开代币 CDN 高清图（针对动态新增的长尾山寨币）
 *   4. 最终兜底首字母微徽标（永不破图）
 */
import { computed, ref, watch } from 'vue';
import { getCryptoLocalUrl, getCryptoSvg, getCryptoCdnUrl, cleanSymbol } from '../../config/cryptoLogos';

const props = withDefaults(
  defineProps<{
    symbol?: string;
    size?: number;
    alt?: string;
  }>(),
  { size: 20 }
);

const sym = computed(() => cleanSymbol(props.symbol));
const localUrl = computed(() => (sym.value ? getCryptoLocalUrl(sym.value) : null));
const builtInSvg = computed(() => (sym.value ? getCryptoSvg(sym.value) : null));
const cdnUrl = computed(() => (sym.value && !localUrl.value && !builtInSvg.value ? getCryptoCdnUrl(sym.value) : null));

const localFailed = ref(false);
const cdnFailed = ref(false);

// 当 symbol 变更时复位图片错误状态
watch(sym, () => {
  localFailed.value = false;
  cdnFailed.value = false;
});

const HUES = [212, 265, 32, 160, 340, 190, 285, 100, 12, 230];

const hue = computed(() => {
  const s = sym.value || 'R';
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
  return HUES[h % HUES.length];
});
</script>

<template>
  <!-- 1. 本地官方高清真实代币图（通过 /assets/ 打包，含真实柴犬 DOGE） -->
  <img
    v-if="localUrl && !localFailed"
    :src="localUrl"
    :alt="alt || ''"
    loading="eager"
    class="crypto-logo inline-flex shrink-0 select-none rounded-full object-cover"
    :style="{
      width: size + 'px',
      height: size + 'px',
    }"
    @error="localFailed = true"
    aria-hidden="true"
  />

  <!-- 2. 内置官方高精度矢量 SVG（本地图未命中或失败时承接） -->
  <span
    v-else-if="builtInSvg"
    class="crypto-logo inline-flex shrink-0 select-none items-center justify-center rounded-full overflow-hidden leading-none"
    :style="{
      width: size + 'px',
      height: size + 'px',
    }"
    :title="sym"
    aria-hidden="true"
    v-html="builtInSvg"
  />

  <!-- 3. 长尾山寨币 CDN 真实图 -->
  <img
    v-else-if="cdnUrl && !cdnFailed"
    :src="cdnUrl"
    :alt="alt || ''"
    loading="lazy"
    class="crypto-logo inline-flex shrink-0 select-none rounded-full object-cover"
    :style="{
      width: size + 'px',
      height: size + 'px',
    }"
    @error="cdnFailed = true"
    aria-hidden="true"
  />

  <!-- 4. 最终兜底首字母微徽标 -->
  <span
    v-else
    class="crypto-logo inline-flex shrink-0 select-none items-center justify-center rounded-md font-bold font-mono"
    :style="{
      width: size + 'px',
      height: size + 'px',
      fontSize: Math.max(11, size * 0.52) + 'px',
      lineHeight: 1,
      backgroundColor: `hsl(${hue} 60% 50% / 0.14)`,
      color: `hsl(${hue} 72% 74%)`,
      border: `1px solid hsl(${hue} 60% 55% / 0.3)`,
    }"
    aria-hidden="true"
  >
    {{ (sym || '?').slice(0, 1) }}
  </span>
</template>

<style scoped>
.crypto-logo {
  vertical-align: middle;
}
.crypto-logo :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
