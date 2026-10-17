/**
 * evolutionStatus.ts · 自进化演进状态解析与渲染样式映射单一事实源
 *
 * 后端自进化引擎（self_improvement_engine / evolution_shield）规范状态枚举：
 * - ADD: 新增启发式心法
 * - REVISE: 修订现有心法
 * - INVALIDATE: 淘汰作废心法
 * - NO_CHANGE: 维持现状
 * 历史及前台兼容别名：
 * - CHANGED / EVOLVED / UPDATED: 发生认知演进
 * - RUNNING: 复盘进行中
 * - FAILED 或包含 llm_error: 复盘失败
 */

export const EVOLVED_STATUS_SET = new Set([
  'CHANGED',
  'EVOLVED',
  'ADD',
  'REVISE',
  'INVALIDATE',
  'UPDATED',
]);

export type EvolutionCategory = 'EVOLVED' | 'NO_CHANGE' | 'RUNNING' | 'FAILED' | 'UNKNOWN' | 'EMPTY';

export interface EvolutionStatusResolved {
  key: string;
  category: EvolutionCategory;
  hudClass: string;
  hudTextClass: string;
  adminBadgeClass: string;
}

export function resolveEvolutionStatus(
  rawStatus?: unknown,
  rawError?: unknown,
): EvolutionStatusResolved {
  const key = String(rawStatus || '').trim().toUpperCase();
  const errorStr = String(rawError || '').trim();
  const hasError = Boolean(errorStr) || key === 'FAILED';

  if (hasError) {
    return {
      key,
      category: 'FAILED',
      hudClass: 'text-[var(--down)] border-[var(--down-line)] bg-[var(--down-bg)]',
      hudTextClass: 'text-[var(--down)]',
      adminBadgeClass: 'badge-down',
    };
  }

  if (EVOLVED_STATUS_SET.has(key)) {
    return {
      key,
      category: 'EVOLVED',
      hudClass: 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]',
      hudTextClass: 'text-[var(--up)]',
      adminBadgeClass: key === 'INVALIDATE' ? 'badge-warn' : 'badge-up',
    };
  }

  if (key === 'NO_CHANGE') {
    return {
      key,
      category: 'NO_CHANGE',
      hudClass: 'text-[var(--ink-2)] border-[var(--line-1)] bg-[var(--surface-2)]',
      hudTextClass: 'text-[var(--ink-1)]',
      adminBadgeClass: 'badge-info',
    };
  }

  if (key === 'RUNNING') {
    return {
      key,
      category: 'RUNNING',
      hudClass: 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]',
      hudTextClass: 'text-[var(--warn)]',
      adminBadgeClass: 'badge-warn',
    };
  }

  if (key) {
    return {
      key,
      category: 'UNKNOWN',
      hudClass: 'text-[var(--ink-2)] border-[var(--line-1)] bg-[var(--surface-2)]',
      hudTextClass: 'text-[var(--ink-1)]',
      adminBadgeClass: 'badge-info',
    };
  }

  return {
    key: '',
    category: 'EMPTY',
    hudClass: '',
    hudTextClass: 'text-[var(--ink-1)]',
    adminBadgeClass: 'badge-info',
  };
}
