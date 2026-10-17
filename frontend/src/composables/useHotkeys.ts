/**
 * 全局快捷键 —— 'mod+k' 兼容 Cmd/Ctrl；输入框聚焦时默认不触发（可 allowInInput）
 */
import { onMounted, onUnmounted } from 'vue';

type Handler = (e: KeyboardEvent) => void;
type HotkeyMap = Record<string, { handler: Handler; allowInInput?: boolean; preventDefault?: boolean }>;

function comboOf(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push('mod');
  if (e.shiftKey) parts.push('shift');
  if (e.altKey) parts.push('alt');
  // 批 83：原为 `e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase()`
  // —— 两支完全相同（压缩后只剩逗号表达式 `(e.key.length, e.key.toLowerCase())`），
  // 是复制粘贴留下的死代码；且 `e.key` 在少数合成/非常规事件里可能缺失，
  // 直接读 `.length` 会在**全局** keydown 处理器里抛错。统一兜底为字符串。
  parts.push((e.key || '').toLowerCase());
  return parts.join('+');
}

function inEditable(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export function useHotkeys(map: HotkeyMap) {
  function onKey(e: KeyboardEvent) {
    const combo = comboOf(e);
    const entry = map[combo];
    if (!entry) return;
    if (inEditable(e) && !entry.allowInInput) return;
    if (entry.preventDefault !== false) e.preventDefault();
    entry.handler(e);
  }
  onMounted(() => window.addEventListener('keydown', onKey));
  onUnmounted(() => window.removeEventListener('keydown', onKey));
}
