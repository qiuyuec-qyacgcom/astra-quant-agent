/**
 * 提交类动作（保存 / 删除 / 测试连接 / 拉取）的 busy + 统一错误处理（结构优化 F2）。
 *
 * ## 为什么有它
 *
 * 各页的"点一下按钮、跑一段 async、期间禁用按钮"几乎都是手写：
 * ```ts
 * const saving = ref(false)
 * async function save() {
 *   saving.value = true
 *   try { await api('/api/v1/xxx', { method: 'POST' }) ; toast.ok('已保存') }
 *   catch (e: any) { toast.err(e.message) }
 *   finally { saving.value = false }
 * }
 * ```
 * 出错策略同样是各写各的。本 composable 统一为：
 * **`run` 不抛异常**（内部已捕获），一定把消息写进 `error`，并按 `onError` 提示；
 * 未给 `onError` 且未声明 `silent` 时默认弹一条 toast —— 避免"静默失败"这种最难排查的形态。
 *
 * ## 用法
 * ```ts
 * const { run: save, busy: saving } = useAsyncAction(
 *   () => api('/api/v1/xxx', { method: 'POST', body: JSON.stringify(form) }),
 *   { onSuccess: () => toast.ok('已保存') },
 * )
 * // 模板：<button :disabled="saving" @click="save">
 * ```
 *
 * 返回值：成功时返回 `fn` 的结果，失败时返回 `undefined`（并置 `error`）。
 * 需要区分"返回 undefined"与"失败"时请读 `error`。
 */
import { ref, type Ref } from 'vue'
import { useToast } from './useToast'

export interface UseAsyncActionOptions<R> {
  /** 出错时的处理；给了它就不再默认弹 toast */
  onError?: (error: Error) => void
  /** 成功回调（toast.ok 等） */
  onSuccess?: (result: R) => void
  /** 出错时既不弹 toast 也不做别的（仅写 `error`） */
  silent?: boolean
  /**
   * `busy` 的初始值，默认 false。
   * 页面**首次加载**场景应传 true —— 手写样板是 `loading = ref(true)`，
   * 首帧就显示加载态；若 busy 从 false 起步，首帧会先闪一下空态再进入加载态。
   */
  initialBusy?: boolean
}

export interface UseAsyncActionResult<Args extends any[], R> {
  run: (...args: Args) => Promise<R | undefined>
  busy: Ref<boolean>
  error: Ref<string | null>
}

export function useAsyncAction<Args extends any[] = any[], R = any>(
  fn: (...args: Args) => Promise<R>,
  options: UseAsyncActionOptions<R> = {},
): UseAsyncActionResult<Args, R> {
  const toast = useToast()
  const busy = ref(Boolean(options.initialBusy))
  const error = ref<string | null>(null)

  async function run(...args: Args): Promise<R | undefined> {
    busy.value = true
    error.value = null
    try {
      const result = await fn(...args)
      options.onSuccess?.(result)
      return result
    } catch (e: any) {
      const err = e instanceof Error ? e : new Error(String(e))
      const message = err.message || String(e)
      error.value = message
      if (options.onError) options.onError(err)
      else if (!options.silent) toast.err(message)
      return undefined
    } finally {
      busy.value = false
    }
  }

  return { run, busy, error }
}
