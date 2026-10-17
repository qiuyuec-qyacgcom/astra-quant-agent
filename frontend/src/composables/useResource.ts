/**
 * 一次性取数 + loading / error / 重取（结构优化 F2）。
 *
 * ## 为什么有它
 *
 * 20 个管理页里有 17 个各写一遍同样的样板：
 * ```ts
 * const data = ref<any>(null); const loading = ref(true); const errText = ref('')
 * async function load() {
 *   loading.value = true
 *   try { data.value = await api('/api/v1/xxx'); errText.value = '' }
 *   catch (e: any) { errText.value = e.message }
 *   finally { loading.value = false }
 * }
 * onMounted(load)
 * ```
 * 实测 catch 数量：LlmPage 13、SecurityPage 11、NotifyPage 10、PromptStudioPage 10……
 * 更麻烦的是错误处理策略**各页不一**：有的吞、有的 toast、有的只 console。
 *
 * 本 composable 把这段收成一行，并统一策略：错误一律进 `error`（页面照旧渲染），
 * 是否额外提示由 `onError` 决定。
 *
 * ## 与 `useApi` 的关系
 *
 * `useApi()` 的 `loading`/`error` 是**该实例共享**的（多请求会互相覆盖），
 * 所以各页才要自己再写一份。`useResource` 内部新建一个 `useApi()` 实例，
 * 只做 HTTP，不读它的 loading/error —— 每个资源有自己的状态。
 *
 * ## 竞态
 *
 * `reload()` 用自增序号守卫：慢的旧响应不会覆盖后发的新响应。
 * （旧样板没有这层保护，切标的/连点刷新时会出现"旧数据盖新数据"。）
 */
import { getCurrentScope, onScopeDispose, ref, type Ref } from 'vue'
import { useApi } from './useApi'

export interface UseResourceOptions<T> {
  /** 是否立即取一次，默认 true */
  immediate?: boolean
  /** 初始值（取数完成前 data 的内容），默认 undefined */
  initial?: T
  /** 把原始响应转成页面要的形状 */
  transform?: (raw: any) => T
  /** 出错时的额外处理（如 toast）；`error` 无论如何都会被置位 */
  onError?: (error: Error) => void
  /** 轮询间隔（毫秒）；>0 时启用，卸载时自动清理 */
  pollMs?: number
}

export interface UseResourceResult<T> {
  data: Ref<T | undefined>
  loading: Ref<boolean>
  error: Ref<string | null>
  /** 是否至少成功取到过一次（用于区分"空"与"还没取"） */
  loaded: Ref<boolean>
  reload: () => Promise<T | undefined>
}

export function useResource<T = any>(
  path: string | (() => string),
  options: UseResourceOptions<T> = {},
): UseResourceResult<T> {
  const { api } = useApi()
  const data = ref<T | undefined>(options.initial) as Ref<T | undefined>
  const loading = ref(false)
  const error = ref<string | null>(null)
  const loaded = ref(false)

  let seq = 0

  async function reload(): Promise<T | undefined> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const url = typeof path === 'function' ? path() : path
      const raw = await api<any>(url)
      if (current !== seq) return data.value // 已有更新的请求在飞，丢弃本次结果
      data.value = (options.transform ? options.transform(raw) : raw) as T
      loaded.value = true
      return data.value
    } catch (e: any) {
      if (current !== seq) return data.value
      const err = e instanceof Error ? e : new Error(String(e))
      error.value = err.message || String(e)
      options.onError?.(err)
      return data.value
    } finally {
      if (current === seq) loading.value = false
    }
  }

  if (options.immediate) void reload()

  if (options.pollMs && options.pollMs > 0) {
    const timer = window.setInterval(() => void reload(), options.pollMs)
    // 只在组件/effect 作用域内注册清理；脱离作用域使用时由调用方自行负责
    if (getCurrentScope()) onScopeDispose(() => window.clearInterval(timer))
  }

  return { data, loading, error, loaded, reload }
}
