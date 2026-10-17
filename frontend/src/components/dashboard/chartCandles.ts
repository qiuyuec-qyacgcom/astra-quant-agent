/**
 * 行情蜡烛数据拉取与归一（结构优化阶段 4·B3 第五十五刀）。
 *
 * ## 为什么抽出来
 *
 * `ChartWorkstation.vue` 里有**两条**取蜡烛的路径，各自内联了同一套
 * "拼 URL → fetch → 查 `res.ok` → 取 `data.candles` → 转 `KLineData`"：
 *
 * | 位置 | 用途 | 失败时 |
 * |---|---|---|
 * | `setDataLoader({ getBars })` | KLineChart 内部驱动取数 | `console.warn` 后 `callback([], false)` |
 * | `loadCandles()` | 定时静默刷新 | `console.warn` 后保持原图不动 |
 *
 * 两份拷贝的 URL 拼法、字段映射**逐字相同**，但错误处理不同 ——
 * 这正是"想改一处却漏掉另一处"的典型结构。
 * 抽出本模块后，两处只保留各自的错误处理，取数与归一只有一份。
 *
 * ## ⚠️ 行为契约（必须与原实现逐字一致）
 *
 * - URL：`/api/v1/market/${instId}/candles?bar=${period}&limit=150&_t=${Date.now()}`
 *   —— `_t` 是**缓存穿透**参数，`cache: 'no-store'` 不能替代它
 *   （旧实现两者都带，故这里都保留）。
 * - 字段映射：`{ timestamp, open, high, low, close, volume, turnover }`，
 *   其中 `volume = c.vol`、**`turnover = c.vol * c.close`**（不是 `c.turnover`）。
 * - `data.candles` 非数组或为空 → 视为"无数据"，**不是**错误。
 * - HTTP 非 2xx → 抛 `Error(\`HTTP ${status}\`)`（沿用旧文案）。
 * - 网络层异常 → 原样上抛（由调用方决定怎么记日志）。
 *
 * ⚠️ `limit=150` 是本模块唯一的"魔法数"，**刻意就地保留字面量**：
 * 本仓约定不把这类数字抽成配置常量，改常数名会让"改了哪个数"更难追。
 */

/**
 * ⚠️ `KLineData` **复用 `klinecharts` 的类型**，不自己再声明一份。
 * 第一版我在本文件里手写了同名字段接口，`vue-tsc` 立刻报
 * "Index signature for type 'string' is missing" —— 两份形状看似相同、
 * 实际不兼容，调用处 `callback(klineList)` 直接编译失败。
 * 类型定义只应有一处。
 */
import type { KLineData } from 'klinecharts'

export type { KLineData }

/** 拉取结果：`candles` 是服务端原始数组，`klineList` 是归一后的图表数据。 */
export interface CandleFetchResult {
  /** 服务端原始蜡烛（可能为空数组）。**原样**返回，不做二次加工。 */
  candles: any[]
  /** 归一后的图表数据，与 `candles` 等长同序。 */
  klineList: KLineData[]
  /** 最后一根收盘价的 `Number()` 结果；无数据时为 `null`。
   *  ⚠️ 可能是 NaN（与原实现一致）——调用方自行决定是否校验。 */
  lastClose: number | null
}

/**
 * 拼取蜡烛的 URL。
 *
 * 独立成函数是为了让 `_t` 缓存穿透参数与 `limit` 只有一处定义 ——
 * 两条取数路径曾经各拼一遍。
 */
export function candlesUrl(instId: string, period: string): string {
  return `/api/v1/market/${instId}/candles?bar=${period}&limit=150&_t=${Date.now()}`
}

/** 把服务端蜡烛归一为 KLineChart 的 `KLineData`（字段名与旧实现逐字一致）。 */
export function toKlineData(candles: any[]): KLineData[] {
  return candles.map((c: any) => ({
    timestamp: c.ts,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.vol,
    turnover: c.vol * c.close,
  }))
}

/**
 * 取一次蜡烛并归一。
 *
 * ⚠️ 本函数**不吞异常**：HTTP 非 2xx 与网络失败都会抛出，
 * 由调用方按各自既有方式来记日志 / 兜底（这正是两条路径唯一的真实差异）。
 *
 * @param instId  标的（已由 `instIdOf` 归一）
 * @param period  周期字面量，如 `'1H'`
 * @param fetchImpl  注入的 fetch —— 便于测试；生产传 `window.fetch`
 */
export async function fetchCandles(
  instId: string,
  period: string,
  fetchImpl: typeof fetch = fetch,
): Promise<CandleFetchResult> {
  const res = await fetchImpl(candlesUrl(instId, period), { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  const candles: any[] = Array.isArray(data?.candles) ? data.candles : []
  if (candles.length === 0) {
    return { candles, klineList: [], lastClose: null }
  }
  const klineList = toKlineData(candles)
  const last = klineList[klineList.length - 1]
  // ⚠️ 与原实现逐字一致：`Number(last.close)` **不加** Number.isFinite 守卫。
  //    旧代码就是直接 `currentPrice.value = Number(lastC.close)`；
  //    close 缺失时会得到 NaN。抽离阶段以**等价性优先**，不夹带行为变更 ——
  //    "要不要挡 NaN" 是独立的行为决策，需另行取证后再改。
  const lastClose = last ? Number(last.close) : null
  return { candles, klineList, lastClose }
}
