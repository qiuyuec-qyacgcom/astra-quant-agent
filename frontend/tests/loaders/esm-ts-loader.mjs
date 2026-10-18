/**
 * ESM 模块解析/加载钩子（供 Node 测试在无 `--experimental-loader` 时用
 * `module.register()` 注入）。
 *
 * 本文件运行在 **loader 线程**里，与测试主线程不共享闭包 —— 配置只能通过
 * `register(specifier, parentURL, { data })` 的 `data` 载荷（结构化克隆）传入，
 * 在 `initialize(data)` 里落到模块级变量。
 *
 * ## 钩子职责
 *
 * 1. `resolve`：给"相对且无扩展名"的 import 补 `.ts` / `.js` / `.vue`，
 *    再补目录 `index.ts` / `index.js` / `index.vue`。Vite 能解析
 *    `./dash` → `./dash/index.ts`，Node 的 ESM 解析器却会抛
 *    `ERR_UNSUPPORTED_DIR_IMPORT`，故目录这一档必不可少。
 * 2. `load`：把 `stubs` 里登记的文件（按 basename）换成受控替身源码，
 *    使测试能把 pinia store / composable 依赖换成桩。
 *
 * ## 为什么不用 `registerHooks`
 *
 * `node:module` 的 `registerHooks` 是 Node **v24+** 才有的 API；本仓实际运行
 * 环境是 Node v22（`node --version` 见 v22.13.x）。`module.register()` 是
 * v22 里登记 loader 的正确入口，二者语义一致，只是后者把钩子拆到独立文件、
 * 用 `data` 传配置。
 */
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

/** 桩表：`basename -> 替身源码`。由 `initialize(data)` 注入。 */
let stubs = {};
/** 桩表作用域：只有位于该根目录下的文件才参与 basename 匹配。 */
let srcRoot = '';

/**
 * loader 线程入口：接收 `register(..., { data })` 传进的配置。
 * `data` 是结构化克隆，只装可序列化字段（字符串/普通对象）。
 */
export function initialize(data) {
  stubs = data?.stubs ?? {};
  srcRoot = data?.srcRoot ?? '';
}

/** 相对、无扩展名 → 依次补 `.ts/.js/.vue`，再补目录 `index.ts/js/vue`。 */
export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && !path.extname(specifier) && context.parentURL) {
    const base = path.dirname(fileURLToPath(context.parentURL));
    for (const ext of ['.ts', '.js', '.vue']) {
      const cand = path.join(base, specifier + ext);
      if (fs.existsSync(cand) && fs.statSync(cand).isFile()) {
        return { url: pathToFileURL(cand).href, shortCircuit: true };
      }
    }
    for (const ext of ['.ts', '.js', '.vue']) {
      const cand = path.join(base, specifier, 'index' + ext);
      if (fs.existsSync(cand) && fs.statSync(cand).isFile()) {
        return { url: pathToFileURL(cand).href, shortCircuit: true };
      }
    }
  }
  return nextResolve(specifier, context);
}

/** 把登记在桩表里的文件替换成替身源码。 */
export async function load(url, context, nextLoad) {
  const file = fileURLToPath(url);
  const name = path.basename(file);

  // 桩表按 basename 匹配；若指定了 srcRoot，则只有根目录下的文件才参与匹配，
  // 避免误伤同名的第三方/系统模块。
  if (stubs[name]) {
    const inScope = !srcRoot || file.startsWith(path.resolve(srcRoot) + path.sep);
    if (inScope) {
      return { format: 'module', shortCircuit: true, source: stubs[name] };
    }
  }
  return nextLoad(url, context);
}
