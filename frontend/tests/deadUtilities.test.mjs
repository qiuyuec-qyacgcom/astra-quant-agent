/**
 * 工具类"静默失效"闸（批 37，承接批 28 的同源缺陷）。
 *
 * ## 守什么
 *
 * 模板里写了类名、Tailwind 却没生成对应规则时，**没有任何报错**：
 * 节点只是"继承父级"。这类缺陷已经发生过两次：
 *
 * - 批 28：`text-3xs/2xs/4xs` 未在 `@theme` 声明 → 385 个节点静默继承 12px；
 * - 批 37：`leading-body` 未声明 → 92 个节点静默继承祖先行高
 *   （落在卡片里是 1.6、落在密集表格里被压成 1.45，取决于祖先而不是自己写的类）。
 *
 * 判据：**模板里的静态/字面类名 − 编译产物里的类选择器 = 死类**。
 *
 * ## 为什么用 AST 而不是正则
 *
 * `:class="{ 'is-up': f.tone }"` 里的 `f.tone` 不是类名；`:class="row.cls"` 更会把
 * 后面属性的引号一并卷进来。正则抽出来的"死类"里全是 `f.tone`、`activeLogTab`
 * 这种假阳性（实测 196 个里 185 个是噪声）。改用 `@vue/compiler-dom` 解析模板，
 * 只对角色的静态 `class` 属性与 `:class` 里的**字符串字面量**取词 —— 差集降到 11 个，
 * 且全部是已知的钩子类。
 *
 * ## 跳过条件
 *
 * 需要编译产物（`dist/assets/*.css`，已 gitignore）。没构建过、或产物比源码旧时
 * 跳过并说明原因 —— 否则纯改源码会误红。
 *
 * 运行：`npm run build && node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { parse } from '@vue/compiler-dom';

const ROOT = path.resolve(import.meta.dirname, '..');
const SRC = path.join(ROOT, 'src');
const DIST = path.join(ROOT, 'dist', 'assets');

/**
 * 已知的"惰性钩子类"：元素身上已有真正生效的类，这些名字没有规则，属历史遗留。
 * 保留是有意的（清掉要动 9 个文件的模板，收益仅是少 9 个字符串）；
 * 新增死类必须显式加进这里，或去把类名修对。
 */
const KNOWN_INERT_HOOKS = new Set([
  // 图表库（TradingView 之类）注入 DOM 的钩子，不能删
  'indicator-dropdown-container',
  'table-scroll-container',
  // 历史遗留：元素已有 .card / .panel 等真类
  'dir',
  'ab-result-body',
  'cn-seatlist',
  'cn-transcript',
  'auth-card-wrap',
  'ov-stream-card',
  'ov-pipe-card',
  'ov-nav-card',
  'ov-audit-card',
]);

const TOKEN = /^[a-zA-Z][a-zA-Z0-9_:\-\[\]\/%,.]*$/;
const BAD = new Set(["'", '"', '{', '}', '(', ')', '?', '=', '>', '&', '|', '!', '+', '*', '`', '$']);

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

/** 模板里的静态 class 与 :class 字符串字面量 */
function templateClasses() {
  const counts = new Map();
  const bump = (c) => {
    if (!c || [...c].some((ch) => BAD.has(ch)) || !TOKEN.test(c)) return;
    counts.set(c, (counts.get(c) || 0) + 1);
  };
  for (const f of vueFiles(SRC)) {
    const sfc = readFileSync(f, 'utf8');
    const m = sfc.match(/<template>([\s\S]*)<\/template>/);
    if (!m) continue;
    let ast;
    try {
      ast = parse(m[1]);
    } catch {
      continue; // 解析不了的模板跳过，别让闸自己变成噪声源
    }
    const visit = (node) => {
      if (node.type === 1) {
        for (const p of node.props) {
          if (p.type === 6 && p.name === 'class' && p.value) {
            for (const c of p.value.content.split(/\s+/)) bump(c);
          } else if (p.type === 7 && p.name === 'class' && p.exp) {
            for (const lit of p.exp.content.match(/'[^']*'|"[^"]*"/g) || []) {
              for (const c of lit.slice(1, -1).split(/\s+/)) bump(c);
            }
          }
        }
      }
      if (Array.isArray(node.children)) for (const k of node.children) if (k && typeof k === 'object' && 'type' in k) visit(k);
    };
    visit(ast);
  }
  return counts;
}

/** 编译产物里的类选择器（反转义 Tailwind 的数字前导转义，如 `\32 xl` → `2xl`） */
function builtSelectors() {
  const sel = new Set();
  for (const n of readdirSync(DIST)) {
    if (!n.endsWith('.css')) continue;
    const css = readFileSync(path.join(DIST, n), 'utf8');
    for (const m of css.matchAll(/\.((?:\\.|[A-Za-z0-9_-])+)/g)) {
      const name = m[1].replace(/\\([0-9a-fA-F]{2}) ?/g, (_, h) => String.fromCharCode(parseInt(h, 16))).replace(/\\/g, '');
      sel.add(name);
    }
  }
  return sel;
}

/** dist 是否存在且不比源码旧 */
function distFresh() {
  try {
    if (!statSync(DIST).isDirectory()) return 'no-dist';
    const css = readdirSync(DIST).filter((n) => n.endsWith('.css'));
    if (!css.length) return 'no-dist';
    let newestCss = 0;
    for (const n of css) newestCss = Math.max(newestCss, statSync(path.join(DIST, n)).mtimeMs);
    let newestSrc = 0;
    for (const f of vueFiles(SRC)) newestSrc = Math.max(newestSrc, statSync(f).mtimeMs);
    for (const f of readdirSync(path.join(SRC, 'styles'))) newestSrc = Math.max(newestSrc, statSync(path.join(SRC, 'styles', f)).mtimeMs);
    return newestSrc > newestCss ? 'stale-dist' : 'ok';
  } catch {
    return 'no-dist';
  }
}

test('模板里的静态类名都能在编译产物里找到规则（死类 = 静默失效）', (t) => {
  const state = distFresh();
  if (state !== 'ok') {
    t.skip(`跳过：${state === 'no-dist' ? '没有 dist 产物' : 'dist 比源码旧'}（先 npm run build）`);
    return;
  }
  const classes = templateClasses();
  const sel = builtSelectors();
  const dead = [...classes.entries()]
    .filter(([c]) => !sel.has(c) && !KNOWN_INERT_HOOKS.has(c) && !/^(is-|has-|js-)/.test(c))
    .sort((a, b) => b[1] - a[1]);
  assert.deepEqual(
    dead,
    [],
    `这些类名在编译产物里没有规则（写对了类名却没有样式，节点会静默继承父级）：\n  ${dead
      .map(([c, n]) => `.${c} ×${n}`)
      .join('\n  ')}\n若是刻意保留的钩子类，请加进本文件的 KNOWN_INERT_HOOKS。`,
  );
  assert.ok(classes.size > 800, `模板类名只解析出 ${classes.size} 个，AST 解析可能失效（防空自检）`);
});
