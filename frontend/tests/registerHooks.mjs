/**
 * `registerHooks` 兼容垫片：把 Node v24 的 `registerHooks` 语义用
 * `module.register()` + 独立 loader 文件（`loaders/esm-ts-loader.mjs`）复现，
 * v22 / v24 通吃。
 *
 * ## 为什么需要这个垫片
 *
 * 前端测试（`http.test.mjs`、`useLlmConfig.test.mjs`）此前 `import { registerHooks }
 * from 'node:module'` —— 那是 **Node v24+** 才有的导出，而本仓实际运行环境是
 * Node v22.13.x，直接 import 会抛 "registerHooks 不是 node:module 的导出"。
 *
 * `module.register(specifier, parentURL, { data })` 是 Node ≥ 22 稳定存在的
 * 登记入口（v24 里 deprecated 但未移除），它把 `resolve`/`load` 钩子装进
 * `loaders/esm-ts-loader.mjs`，配置（桩表 + 作用域根）经 `data` 结构化克隆传入，
 * 在 loader 线程的 `initialize(data)` 落地。
 *
 * ## 用法
 *
 * 本垫片导出 `registerHooks(handlers)`。真正行为由 `handlers.__data` 驱动：
 *
 *     await registerHooks({ __data: { srcRoot, stubs: { 'auth.ts': '...' } } })
 *
 * 之后才能 `import` 需要被钩子处理的模块（`register()` 是异步的）。
 */
import * as nodeModule from 'node:module';

// loader 文件的 file:// URL —— 直接 new URL 即为规范的 file: URL，无需再 pathToFileURL。
const LOADER_URL = new URL('./loaders/esm-ts-loader.mjs', import.meta.url).href;
// `module.register` 是 Node ≥ 22 稳定存在的登记入口；Node v24 新增的
// `registerHooks` 是它的替代品，但 `register` 在 v24 仍可用（deprecated 而非移除）。
// 故这里**只走 `register`**，v22 / v24 通吃，无需按版本分叉。
const registerLoader = nodeModule.register;

/**
 * 登记 ESM 解析/加载钩子。
 *
 * 兼容 `node:module` 的 `registerHooks` 调用形态，但底层统一走 `module.register`
 * + 独立 loader 文件（`loaders/esm-ts-loader.mjs`）。真正行为由 `__data` 驱动，
 * `resolve` / `load` 回调（若有）在 v22 上不会执行、仅作文档性占位。
 *
 * @param {object} handlers `{ resolve?, load?, __data? }`。
 *   `__data` 是传给 loader 的配置：`{ stubs: {basename: source}, srcRoot: string }`。
 * @returns {Promise<void>} resolve 后 loader 才就绪，才可 import 目标模块。
 */
export async function registerHooks(handlers = {}) {
  const { __data = {} } = handlers;
  await registerLoader(LOADER_URL, 'data:', { data: __data });
}
