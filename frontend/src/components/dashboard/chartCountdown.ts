/**
 * K 线周期倒计时（结构优化阶段 3·F4 第三轮抽离）。
 *
 * 原先是 `ChartWorkstation.vue::updateCountdown()` 内联的一段纯算术：按当前周期
 * 算出"距本根 K 线收线还有多久"，再格式化成 `MM:SS`。它**没有任何组件依赖** ——
 * 只吃 `period` 与"此刻的北京时间时分秒"，吐一个字符串。抽出来之后可以单独测，
 * 不必挂载组件。
 *
 * ## 为什么以"北京时间时分秒"为入参，而不是 `Date`
 *
 * 原实现用 `fmtClock(now)` 取北京时间（本仓的时间契约：**展示与周期一律北京时间**，
 * 见 `utils/format`）。若这里收 `Date` 再自己转换，就等于把时间契约复制了一份，
 * 将来契约改了这处会跟着错。故**让调用方传入已转换好的时分秒**，
 * 本模块只做算术，不碰时区。
 *
 * ## 周期口径（与原实现逐条一致）
 *
 * | period | 剩余时间算法 |
 * |---|---|
 * | `15m` | 距下一个 15 分钟刻度 |
 * | `1H` | 距整点 |
 * | `4H` | 距下一个 4 小时刻度（按**当天**小时数取模） |
 * | 其它 | 距当天 24:00 |
 *
 * 结果一律 `Math.max(0, …)`，故刻度处不会出现负数。
 */

/** 支持周期倒计时的周期集合；其余走"距今日结束"分支。 */
export const COUNTDOWN_PERIODS = ['15m', '1H', '4H'] as const

export interface Hms {
  /** 北京时间小时 0–23 */
  hr: number
  /** 分钟 0–59 */
  min: number
  /** 秒 0–59 */
  sec: number
}

/** 距本根 K 线收线的剩余秒数（不小于 0）。 */
export function remainingSeconds(period: string, hms: Hms): number {
  const { hr = 0, min = 0, sec = 0 } = hms
  let remainSec: number
  if (period === '15m') {
    remainSec = (15 - (min % 15)) * 60 - sec
  } else if (period === '1H') {
    remainSec = (60 - min) * 60 - sec
  } else if (period === '4H') {
    remainSec = (4 - (hr % 4)) * 3600 - min * 60 - sec
  } else {
    remainSec = 86400 - (hr * 3600 + min * 60 + sec)
  }
  return Math.max(0, remainSec)
}

/**
 * 秒数 → 倒计时标签。
 *
 * ## ⚠️ 这里修掉了一个**重构时发现的既有缺陷**
 *
 * 原实现是：
 * ```ts
 * const m = Math.floor((remainSec % 3600) / 60)
 * const s = remainSec % 60
 * return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
 * ```
 * `remainSec % 3600` **把小时整个丢掉**，于是"剩余 ≥ 60 分钟"时显示成 `00:00`：
 *
 * | 周期 | 时刻 | 实际剩余 | 原实现显示 | 修复后 |
 * |---|---|---|---|---|
 * | `1H` | 整点 | 3600 s | **00:00** | 60:00 |
 * | `4H` | 每段开头 | 14400 s | **00:00** | 240:00 |
 * | `1D` | 12:00 | 43200 s | **00:00** | 720:00 |
 * | `15m` | 任意 | ≤ 900 s | 正确 | — |
 *
 * 危害：倒计时在整点/4H 段开头显示 `00:00`，读起来是"马上收线"，
 * 与真实剩余时间完全相反。**四个周期里 15m 恰好不受影响**
 * （上限 900 秒，永远不走小时位），所以这个缺陷长期没被发现。
 *
 * 修复方式：**分钟不再对 60 取模**，有小时就显示成 `240:00` 这种形态 ——
 * 与原实现的 `MM:SS` 形态一致（宽度自然增长），不引入新的分隔符。
 * 列宽由 `.num` 等宽字体承担，视觉影响仅限于文字本身。
 */
export function formatCountdown(remainSec: number): string {
  const totalMinutes = Math.floor(remainSec / 60)
  const s = remainSec % 60
  return `${String(totalMinutes).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** 一步到位：周期 + 此刻北京时间时分秒 → `MM:SS`。 */
export function countdownLabel(period: string, hms: Hms): string {
  return formatCountdown(remainingSeconds(period, hms))
}
