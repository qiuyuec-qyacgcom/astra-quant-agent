/**
 * K 线样式表（结构优化阶段 3·F4 抽离）。
 *
 * 这是 `ChartWorkstation.vue`（原 1285 行）里最大的一块**纯**逻辑：189 行、
 * 只有 3 个自由变量（`isDark` / `legendRule` / `tok`）。抽出来后既不碰生命周期、
 * 也不碰图表实例，行为可证不变。
 *
 * 刻意**不**连同 `initChart` / `updatePriceLines` / `loadCandles` / `syncIndicators`
 * 一起抽成 `useChartRenderer.ts` / `useChartOverlays.ts`：那条路径持有图表实例，
 * 与 `onMounted`/`onUnmounted`/`watch` 时序强耦合，而前端没有测试、渲染结果
 * 无法自动验证。研究文档 F4 自己也标注了"需人工过一遍 K 线工位" ——
 * 在有人目视验证之前，不动渲染路径。
 *
 * ⚠️ 批 13：自由变量由 3 个降为 **2 个**（`legendRule` / `tok`）。
 * 原先散落在此的 `dark ? A : B` 三元与十六进制字面量已收口到 `tokens.css`
 * 的 `--chart-*` 主题化色板，颜色一律经 `tok()` 在渲染时取解析值。
 * 主题切换的正确性不变：组件侧的 `watch([isDark, cvd])` 仍会在换肤时重跑本函数，
 * 而 `tok()` 读到的是刚更新过的 `data-theme`。canvas 无法解析 `var()`，
 * 故这里**必须**继续用 `tok()`，不能直接写 CSS 变量引用。
 */

/** 图表网格/蜡烛/指标线的配色与形态，随明暗主题切换 */
export function chartStyles(
  legendRule: () => 'always',
  tok: (name: string) => string,
): any {
  return {
    grid: {
      show: true,
      horizontal: {
        show: true,
        size: 1,
        color: tok('--chart-grid'),
        style: 'solid',
      },
      vertical: {
        show: false, // 隐藏垂直杂乱网格
      },
    },
    candle: {
      type: 'candle_solid',
      margin: {
        top: 72,
        bottom: 16,
      },
      bar: {
        upColor: tok('--up'),
        downColor: tok('--down'),
        noChangeColor: tok('--ink-3'),
        upBorderColor: tok('--up'),
        downBorderColor: tok('--down'),
        noChangeBorderColor: tok('--ink-3'),
        upWickColor: tok('--up'),
        downWickColor: tok('--down'),
        noChangeWickColor: tok('--ink-3'),
      },
      priceMark: {
        show: true,
        high: {
          show: false,
          color: tok('--ink-2'),
          textOffset: 4,
          textSize: 10,
        },
        low: {
          show: false,
          color: tok('--ink-2'),
          textOffset: 4,
          textSize: 10,
        },
        last: {
          show: true,
          upColor: tok('--up'),
          downColor: tok('--down'),
          noChangeColor: tok('--ink-3'),
          line: {
            show: true,
            style: 'dashed',
            dashedValue: [4, 4],
            size: 1,
          },
          text: {
            show: true,
            size: 11,
            paddingLeft: 4,
            paddingTop: 2,
            paddingRight: 4,
            paddingBottom: 2,
            color: tok('--ink-1'),
          },
        },
      },
      tooltip: {
        showRule: legendRule(),
        showType: 'standard',
        text: {
          size: 10,
          family: 'JetBrains Mono, monospace',
          color: tok('--ink-2'),
        },
      },
    },
    indicator: {
      tooltip: {
        showRule: legendRule(),
        showType: 'standard',
      },
      ohlc: {
        upColor: tok('--up'),
        downColor: tok('--down'),
        noChangeColor: tok('--ink-3'),
      },
      lines: [
        { style: 'solid', smooth: false, size: 1.5, color: tok('--chart-ma') }, // MA5 / 黄
        { style: 'solid', smooth: false, size: 1.5, color: tok('--chart-ema') }, // MA10 / 蓝
        { style: 'solid', smooth: false, size: 1.5, color: tok('--chart-kdj') }, // MA20 / 紫
        { style: 'solid', smooth: false, size: 1.5, color: tok('--down') },
        { style: 'solid', smooth: false, size: 1.5, color: tok('--up') },
      ],
      lastValueMark: {
        show: true,
        text: {
          show: true,
          size: 10,
          paddingLeft: 3,
          paddingTop: 1,
          paddingRight: 3,
          paddingBottom: 1,
          color: tok('--ink-1'),
        },
      },
    },
    xAxis: {
      show: true,
      size: 'auto',
      axisLine: {
        show: true,
        color: tok('--surface-3'),
        size: 1,
      },
      tickText: {
        show: true,
        color: tok('--ink-3'),
        family: 'JetBrains Mono, monospace',
        size: 10,
      },
      tickLine: {
        show: true,
        size: 1,
        length: 3,
        color: tok('--surface-3'),
      },
    },
    yAxis: {
      show: true,
      size: 'auto',
      position: 'right',
      type: 'normal',
      inside: false,
      gap: {
        top: 0.08,
        bottom: 0.08,
      },
      axisLine: {
        show: true,
        color: tok('--surface-3'),
        size: 1,
      },
      tickText: {
        show: true,
        color: tok('--ink-2'),
        family: 'JetBrains Mono, monospace',
        size: 11,
      },
      tickLine: {
        show: false,
      },
    },
    separator: {
      size: 1,
      color: tok('--surface-3'),
      fill: true,
      activeBackgroundColor: tok('--chart-grid-active'),
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: tok('--ink-3'),
        },
        text: {
          show: true,
          color: tok('--ink-1'),
          size: 11,
          family: 'JetBrains Mono, monospace',
          backgroundColor: tok('--chart-crosshair-h'),
        },
      },
      vertical: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: tok('--ink-3'),
        },
        text: {
          show: true,
          color: tok('--ink-1'),
          size: 10,
          family: 'JetBrains Mono, monospace',
          backgroundColor: tok('--chart-crosshair-v'),
        },
      },
    },
  }
}
