/**
 * K 线工位的指标目录（结构优化阶段 3·F4 第三轮抽离）。
 *
 * ## 为什么抽
 *
 * 这五个导出原先内嵌在 `ChartWorkstation.vue` 里，是一张**纯配置表**：
 * 指标的 key / 名称 / 说明 / 颜色 / 默认参数 / 是否副图，以及默认开启哪些。
 * 它们没有任何组件依赖，放在组件里只是占体量；抽出来之后
 * `syncIndicators()` 的"要同步哪些指标"这一半也就变成可读的声明。
 *
 * ## 行为等价的要点
 *
 * - `mainIndicators` / `subIndicators` 是**模块级常量数组**：模块只被求值一次，
 *   故组件拿到的仍是**同一个数组实例**（原实现也是模块级 `const`），
 *   `forEach`/`v-for` 的语义完全不变；
 * - `defaultParams` 缺失的项保持**不设该键**（而不是设成 `[]`）——
 *   原实现里 `syncIndicators()` 用 `ind.defaultParams || []` 兜底，
 *   若这里补成 `[]`，`||` 的结果虽然相同，但数据的"有无"变了，
 *   将来若有人改成 `??` 就会分叉。故原样保留"有的有、有的没有"。
 * - `DEFAULT_ACTIVE_INDICATORS` 是一个**对象字面量**，`ref()` 会把它变成
 *   响应式副本，组件仍可整体替换（`toggleIndicatorKey` 就是这么做的）。
 */

/** 主图叠加指标 (Overlay on Main Candle Pane) */
export interface IndicatorOption {
  key: string
  name: string
  label: string
  color: string
  defaultParams?: any[]
  isSub: boolean
}

/** 主图叠加指标：全部挂在 candle_pane 上，可多指标叠加共存 */
export const mainIndicators: IndicatorOption[] = [
  { key: 'VWAP', name: 'VWAP', label: 'VWAP', color: 'var(--chart-vwap)', isSub: false },
  { key: 'MA', name: 'MA', label: 'MA', color: 'var(--chart-ma)', defaultParams: [5, 10, 20], isSub: false },
  { key: 'EMA', name: 'EMA', label: 'EMA', color: 'var(--chart-ema)', defaultParams: [12, 26, 50], isSub: false },
  { key: 'BOLL', name: 'BOLL', label: 'BOLL', color: 'var(--chart-boll)', defaultParams: [20, 2], isSub: false },
  { key: 'SAR', name: 'SAR', label: 'SAR', color: 'var(--chart-sar)', isSub: false },
]

/** 副图独立窗格指标 (Sub Panes) */
export const subIndicators: IndicatorOption[] = [
  { key: 'VOL', name: 'VOL', label: 'VOL', color: 'var(--chart-vol)', isSub: true },
  { key: 'MACD', name: 'MACD', label: 'MACD', color: 'var(--chart-macd)', defaultParams: [12, 26, 9], isSub: true },
  { key: 'RSI', name: 'RSI', label: 'RSI', color: 'var(--chart-rsi)', defaultParams: [6, 12, 24], isSub: true },
  { key: 'KDJ', name: 'KDJ', label: 'KDJ', color: 'var(--chart-kdj)', defaultParams: [9, 3, 3], isSub: true },
  { key: 'OBV', name: 'OBV', label: 'OBV', color: 'var(--chart-obv)', isSub: true },
  { key: 'WR', name: 'WR', label: 'WR', color: 'var(--chart-wr)', defaultParams: [14], isSub: true },
]

/** 默认激活指标：默认开启 VOL 与 VWAP */
export const DEFAULT_ACTIVE_INDICATORS: Record<string, boolean> = {
  VWAP: true,
  VOL: true,
  MA: false,
  EMA: false,
  BOLL: false,
  SAR: false,
  MACD: false,
  RSI: false,
  KDJ: false,
  OBV: false,
  WR: false,
}

/** 目录里所有指标的 key（主图 + 副图），供一致性校验使用。 */
export function allIndicatorKeys(): string[] {
  return [...mainIndicators, ...subIndicators].map((ind) => ind.key)
}
