/**
 * 过渡时长与缓动必须取令牌（批 93）。
 *
 * ## 实测到的漂移
 *
 * 全站 59 条 `transition:` 声明里 **55 条**已经写成
 * `var(--dur-fast|base|slow)` + `var(--ease-out)`，只有两个文件写了字面值：
 *
 * | 位置 | 原文 | 问题 |
 * |---|---|---|
 * | `ToastHost .toast-enter-active` | `transform 0.22s var(--ease-out)` | 0.22s 不在刻度上（120/180/240ms） |
 * | `ToastHost .toast-leave-active` | `all 0.18s ease` | 裸 `ease`，与全站曲线不一致 |
 * | `ToastHost .toast-move` | `transform 0.2s ease` | 0.2s 不在刻度上 + 裸 `ease` |
 * | `TrajectoryPanel .slide-right-*` | `transform 0.24s cubic-bezier(0.16, 1, 0.3, 1)` | **就是 `--dur-slow` + `--ease-out` 的字面写法** |
 * | `TrajectoryPanel .fade-*` | `opacity 0.2s ease` | 同前 |
 * | `base.css .fade-*`（全局） | `opacity var(--dur-base) ease` | 裸 `ease` |
 * | `OverviewPage .ov-gauge-fill` | `width var(--dur-base) ease` | 裸 `ease` |
 *
 * 最刺眼的一条：**同一个 toast 的进 / 出 / 移动用了 0.22s / 0.18s / 0.2s 三个不同时长**。
 *
 * ## 判据口径
 *
 * 1. `transition:` 声明的**时长**必须来自 `var(--dur…)`，不许写 `0.2s` 这类字面值
 *    （`trajectory` 那条正是令牌值的字面复制品 —— 复制出来的值不会跟令牌一起变）。
 * 2. **若显式写了缓动，就必须是 `var(--ease…)`**；不写缓动（用浏览器默认）不算违规
 *    —— 全站多数声明本就不写缓动，强行要求会引发大范围动效改动，超出本批范围。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

export function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(?<!:)\/\/[^\n]*/g, '');
}

function sources(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(vue|css)$/.test(n)) out.push(p);
  }
  return out;
}

/** 抽出全部 `transition:` 简写声明 → [{rel, decl}]。 */
export function transitionDecls() {
  const out = [];
  for (const f of sources(SRC)) {
    const text = stripComments(readFileSync(f, 'utf8'));
    const rel = path.relative(SRC, f);
    for (const m of text.matchAll(/transition\s*:\s*([^;{}]+)/g)) {
      out.push({ rel, decl: m[1].replace(/\s+/g, ' ').trim() });
    }
  }
  return out;
}

/** 这条声明是否写死了时长（字面 `0.2s` / `180ms` 等）。 */
export function hasLiteralTime(decl) {
  return /(^|[\s,])[-+]?\d*\.?\d+m?s($|[\s,])/.test(decl);
}

/** 这条声明是否写了裸缓动关键字（`ease` / `ease-out` / `linear` …）。 */
export function hasBareEasing(decl) {
  // var(--ease-out) 里的 `ease-out` 前面是 `-`，用 (?<![\w-]) 排除
  return /(?<![\w-])(ease-in-out|ease-in|ease-out|ease|linear|step-start|step-end)(?![\w-])/.test(decl);
}

test('全站 transition 声明：时长必须来自令牌，不许写字面值', () => {
  const bad = transitionDecls().filter((t) => hasLiteralTime(t.decl));
  assert.deepEqual(
    bad.map((b) => `${b.rel}: ${b.decl}`),
    [],
    `这些过渡写死了时长（应改用 var(--dur-fast|base|slow)）：\n  ${bad.map((b) => `${b.rel}: ${b.decl}`).join('\n  ')}`,
  );
});

test('全站 transition 声明：显式写的缓动必须是令牌', () => {
  const bad = transitionDecls().filter((t) => hasBareEasing(t.decl));
  assert.deepEqual(
    bad.map((b) => `${b.rel}: ${b.decl}`),
    [],
    `这些过渡写了裸缓动关键字（应改用 var(--ease-out)）：\n  ${bad.map((b) => `${b.rel}: ${b.decl}`).join('\n  ')}`,
  );
});

test('回归锚点：五处已收口的过渡不得退回字面值', () => {
  const all = transitionDecls().map((t) => t.decl).join('\n');
  // 曾经的刻度外时长 / 字面曲线
  for (const gone of ['0.22s', '0.18s', '0.2s ease', '0.24s cubic-bezier', 'var(--dur-base) ease']) {
    assert.ok(!all.includes(gone), `字面值回潮：${gone}`);
  }
  // 改后必须存在的三条
  assert.ok(all.includes('transform var(--dur-base) var(--ease-out)'), 'ToastHost 进入过渡不见了');
  assert.ok(all.includes('all var(--dur-base) var(--ease-out)'), 'ToastHost 离开过渡不见了');
  assert.ok(all.includes('transform var(--dur-slow) var(--ease-out)'), 'TrajectoryPanel 滑入过渡不见了');
  assert.ok(all.includes('opacity var(--dur-base) var(--ease-out)'), 'fade 过渡不见了');
});

test('令牌本身必须存在（判据依赖的 4 个值）', () => {
  const tokens = readFileSync(path.join(SRC, 'styles', 'tokens.css'), 'utf8');
  for (const [name, val] of [['--dur-fast', '120ms'], ['--dur-base', '180ms'], ['--dur-slow', '240ms']]) {
    assert.match(tokens, new RegExp(`${name}:\\s*${val}`), `${name} 应为 ${val}`);
  }
  assert.match(tokens, /--ease-out:\s*cubic-bezier\(0\.16,\s*1,\s*0\.3,\s*1\)/, '--ease-out 曲线变了');
});

test('判据自检：字面时长与裸缓动的识别', () => {
  assert.equal(hasLiteralTime('transform 0.2s var(--ease-out)'), true);
  assert.equal(hasLiteralTime('transform 180ms var(--ease-out)'), true);
  assert.equal(hasLiteralTime('transform var(--dur-base) var(--ease-out)'), false);
  assert.equal(hasBareEasing('transform var(--dur-base) ease'), true);
  assert.equal(hasBareEasing('transform var(--dur-base) ease-out'), true);
  assert.equal(hasBareEasing('transform var(--dur-base) var(--ease-out)'), false, 'var(--ease-out) 不是裸关键字');
  assert.equal(hasBareEasing('transform var(--dur-base) var(--ease-in-out)'), false);
  // 注释里的样例行不得被解析
  assert.equal(transitionDecls().filter((t) => t.decl.includes('样品里写着 0.2s')).length, 0);
});
