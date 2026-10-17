/**
 * 圆角必须取令牌；`.tabs` / `.meter` 两个死家族不得回潮（批 94）。
 *
 * ## 实测
 *
 * 全站 `border-radius` 取值共 12 种，其中 5 个令牌家族占了 113 处
 * （`--r-ctl` 54 / `--r-xs` 35 / `--r-pill` 13 / `--r-card` 9 / `--r-float` 2），
 * `50%` 7 处（正圆）、`0` 2 处（嵌入式）。
 * **只剩 5 处字面 px**，集中在两个文件：
 *
 * | 位置 | 原值 | 处理 |
 * |---|---|---|
 * | `.tabs button[aria-selected]::after`（2px 高的指示条） | `1px` | 该家族**已死**，整族删除 |
 * | `.meter` / `.meter > i`（4px 高的轨道） | `2px` | 该家族**已死**，整族删除 |
 * | `LoginPage .auth-card` | `14px` | **整个半径刻度（4/8/10/12/16/胶囊）里没有 14**，收敛到 `--r-card`(10px) |
 * | `LoginPage .auth-logo-box` | `10px` | 就是 `--r-card` 的值 → 改用令牌（渲染完全相同） |
 *
 * ## 死家族是怎么确认的
 *
 * · `class="tabs"` 全仓 **0 处**（唯一近似命中是 `activeTab` / `tab.key` 标识符与
 *   i18n 的 `nav.tabs.*` 键）；其它 CSS 文件也不引用 `.tabs`。
 * · `meter` 全仓只命中英文单词（`para**meter**s` / `differ`），无任何模板或脚本用法。
 *
 * 故按批 88 删 `.page-head`、批 78 删 `.trace` 的同一处理整族删除
 * （10 条规则 / 39 条声明 / 65 行 / 1,299 字节），并在此钉死不得回潮。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const COMPONENTS_CSS = path.join(SRC, 'styles', 'components.css');
const TOKENS_CSS = path.join(SRC, 'styles', 'tokens.css');

/** 允许的字面圆角（值=理由）。 */
export const RADIUS_ALLOWED = {
  '50%': '正圆（头像 / 圆点）：非正方形时与 --r-pill 不等价，必须用百分比',
  '0': '嵌入式：贴在父容器边缘时不画自己的圆角',
};

/** 已删除的两个死家族。 */
export const DEAD_FAMILIES = ['.tabs', '.meter'];

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

/** 收集全部 `border-radius: <值>`。 */
export function radiusDecls() {
  const out = [];
  for (const f of sources(SRC)) {
    const text = stripComments(readFileSync(f, 'utf8'));
    const rel = path.relative(SRC, f);
    for (const m of text.matchAll(/border-radius\s*:\s*([^;{}]+)/g)) {
      out.push({ rel, value: m[1].replace(/\s+/g, ' ').trim() });
    }
  }
  return out;
}

/** 这个圆角值是否合法（含令牌，或在允许清单里）。 */
export function isAllowedRadius(value) {
  if (value.includes('var(--r-')) return true;
  return Object.keys(RADIUS_ALLOWED).includes(value);
}

test('半径刻度令牌必须齐全（判据依赖）', () => {
  const tokens = readFileSync(TOKENS_CSS, 'utf8');
  for (const name of ['--r-xs', '--r-ctl', '--r-card', '--r-float', '--r-pill']) {
    assert.match(tokens, new RegExp(`${name}:`), `tokens.css 缺少 ${name}`);
  }
});

test('全站 border-radius 必须取令牌（只允许 50% 与 0 两个字面值）', () => {
  const bad = radiusDecls().filter((r) => !isAllowedRadius(r.value));
  assert.deepEqual(
    bad.map((b) => `${b.rel}: ${b.value}`),
    [],
    `这些圆角没走令牌（刻度：--r-xs 4px / --r-ctl 8px / --r-card 10px / --r-float 12px / --r-pill）：\n  ${bad
      .map((b) => `${b.rel}: ${b.value}`)
      .join('\n  ')}`,
  );
});

test('回归锚点：LoginPage 两处已收口，14px / 10px 字面值不得回潮', () => {
  const login = stripComments(readFileSync(path.join(SRC, 'views/admin/LoginPage.vue'), 'utf8'));
  assert.match(login, /\.auth-card\s*\{[^}]*border-radius:\s*var\(--r-card\)/, '.auth-card 应使用 --r-card');
  assert.match(login, /\.auth-logo-box\s*\{[^}]*border-radius:\s*var\(--r-card\)/, '.auth-logo-box 应使用 --r-card');
  const all = radiusDecls().map((r) => r.value);
  for (const gone of ['14px', '10px', '2px', '1px']) {
    assert.ok(!all.includes(gone), `字面圆角回潮：${gone}`);
  }
});

test('死家族 .tabs / .meter 不得回潮（CSS 与模板两侧都查）', () => {
  const bad = [];
  // ① CSS 里不得再有这两个家族的规则
  for (const f of sources(SRC)) {
    const rel = path.relative(SRC, f);
    for (const m of stripComments(readFileSync(f, 'utf8')).matchAll(/([^{}]+)\{/g)) {
      const sel = m[1].trim().split('\n').pop().trim();
      for (const fam of DEAD_FAMILIES) {
        if (new RegExp('\\' + fam + '(?![\\w-])').test(sel)) bad.push(`${rel} 又出现 ${fam} 规则：${sel.slice(0, 50)}`);
      }
    }
  }
  // ② 模板里不得再挂这两个类名
  for (const f of sources(SRC)) {
    if (!f.endsWith('.vue')) continue;
    const rel = path.relative(SRC, f);
    for (const m of stripComments(readFileSync(f, 'utf8')).matchAll(/(?:class|:class)\s*=\s*(["'])(.*?)\1/gs)) {
      for (const fam of DEAD_FAMILIES) {
        const cls = fam.slice(1);
        if (new RegExp('(?<![\\w-])' + cls + '(?![\\w-])').test(m[2])) bad.push(`${rel} 模板又挂 ${cls}`);
      }
    }
  }
  assert.deepEqual([...new Set(bad)], [], `死家族回潮：\n  ${[...new Set(bad)].join('\n  ')}`);
});

test('判据自检：令牌 / 允许清单 / 字面值的识别', () => {
  assert.equal(isAllowedRadius('var(--r-card)'), true);
  assert.equal(isAllowedRadius('var(--r-ctl) var(--r-ctl) 0 0'), true, '多值里含令牌即可');
  assert.equal(isAllowedRadius('50%'), true);
  assert.equal(isAllowedRadius('0'), true);
  assert.equal(isAllowedRadius('14px'), false);
  assert.equal(isAllowedRadius('2px'), false);
  assert.equal(isAllowedRadius('0.5rem'), false, 'rem 也不是本项目的半径刻度');
  // 注释里的样例行不得被解析
  assert.equal(stripComments('/* border-radius: 14px; */').includes('14px'), false);
});
