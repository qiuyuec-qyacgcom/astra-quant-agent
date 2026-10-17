/**
 * 全局轻提示服务 —— 替换全部手写 bannerMsg/内联横幅
 * 用法：const toast = useToast(); toast.ok('已保存'); toast.err('网络错误', '重试建议文案');
 * 渲染：App.vue 挂 <ToastHost />
 *
 * ## 批 108 修的两个「说了但做不到」的地方
 *
 * 1. **`0 = 不自动消失` 到不了**：原来是 `const ttl = duration || 默认值`，
 *    `0` 是假值 → 传 0 反而拿到默认时长；而且公开 API 只收 `(title, desc)`，
 *    **170 个调用点没有一个能设时长**（`duration` 形参与 `ToastItem.duration`
 *    等于死 API）。现在改成 `duration === undefined ? 默认 : duration`，
 *    并把时长开放到公开 API 上，注释里的承诺才成立。
 * 2. **悬停/聚焦不暂停**：默认 3.2s（错误 6s）到点就走，长一点的消息
 *    （尤其带第二行建议文案的错误）经常读不完。现在鼠标移入或键盘焦点进入
 *    提示区就**暂停倒计时**，移出/离开后按**剩余时间**继续；
 *    暂停期间本该到期的，恢复时立即消失（不会「暂停一下就永久留住」）。
 *
 * 计时器用具名 handle 记录并用 `clearTimeout` 收尾：手动关闭 / 超出同屏上限
 * 被挤掉的条目都会连计时器一起清掉。
 */
import { ref, readonly } from 'vue';

export type ToastKind = 'ok' | 'err' | 'warn' | 'info';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  desc?: string;
  /** 毫秒；0 = 不自动消失 */
  duration: number;
}

const items = ref<ToastItem[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const deadline = new Map<number, number>();
const leftMs = new Map<number, number>();
let seq = 0;
let paused = false;

const DEFAULT_MS: Record<ToastKind, number> = { ok: 3200, err: 6000, warn: 3200, info: 3200 };

function forget(id: number) {
  const h = timers.get(id);
  if (h !== undefined) clearTimeout(h);
  timers.delete(id);
  deadline.delete(id);
  leftMs.delete(id);
}

/** 按「剩余时间」上计时器；duration 为 0 表示常驻，不上计时器。 */
function arm(it: ToastItem) {
  const left = leftMs.get(it.id) ?? it.duration;
  if (left <= 0) return;
  deadline.set(it.id, Date.now() + left);
  timers.set(it.id, setTimeout(() => dismiss(it.id), left));
}

function push(kind: ToastKind, title: string, desc?: string, duration?: number): number {
  const id = ++seq;
  const ttl = duration === undefined ? DEFAULT_MS[kind] : duration;
  const it: ToastItem = { id, kind, title, desc, duration: ttl };
  items.value.push(it);
  leftMs.set(id, ttl);
  // 同屏最多 4 条，超出丢最旧
  if (items.value.length > 4) {
    for (const dropped of items.value.splice(0, items.value.length - 4)) forget(dropped.id);
  }
  if (!paused) arm(it);
  return id;
}

function dismiss(id: number) {
  forget(id);
  const i = items.value.findIndex((x) => x.id === id);
  if (i >= 0) items.value.splice(i, 1);
}

/** 暂停所有倒计时（鼠标移入提示区 / 焦点进入时调用）。 */
function pause() {
  if (paused) return;
  paused = true;
  for (const [id, h] of [...timers]) {
    clearTimeout(h);
    timers.delete(id);
    leftMs.set(id, Math.max(0, (deadline.get(id) ?? 0) - Date.now()));
  }
}

/** 恢复倒计时（鼠标移出 / 焦点离开时调用）；暂停期间已到期的立即消失。 */
function resume() {
  if (!paused) return;
  paused = false;
  for (const it of items.value) {
    if (timers.has(it.id)) continue;
    if (it.duration > 0 && (leftMs.get(it.id) ?? it.duration) <= 0) dismiss(it.id);
    else arm(it);
  }
}

export function useToast() {
  return {
    items: readonly(items),
    /** `duration` 省略用默认（ok/warn/info 3200ms，err 6000ms）；传 0 = 不自动消失。 */
    ok: (title: string, desc?: string, duration?: number) => push('ok', title, desc, duration),
    err: (title: string, desc?: string, duration?: number) => push('err', title, desc, duration),
    warn: (title: string, desc?: string, duration?: number) => push('warn', title, desc, duration),
    info: (title: string, desc?: string, duration?: number) => push('info', title, desc, duration),
    dismiss,
    pause,
    resume,
  };
}
