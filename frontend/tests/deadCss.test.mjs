/**
 * 死 CSS 探针（批 95）。
 *
 * ## 起因
 *
 * 批 94 追「字面圆角」时发现 `.meter`／`.tabs` 两个家族**整个是死代码** ——
 * 于是把探针一般化：**全局样式表里定义的每个类名，是否在任何模板/脚本里出现过？**
 *
 * 首轮跑出 24 个零引用类名，逐条核实后：
 *
 * | 类别 | 处理 |
 * |---|---|
 * | 4 个 Vue `<Transition name="fade"\|"pop">` **运行时生成**的类 | 保留（模板里写的是 `name=`，不是类名） |
 * | `route-enter` | 删除（无 `name="route"`，也没有任何 `*-class=` 自定义过渡属性） |
 * | 其余 19 个 | 删除 |
 *
 * 删除用**最小差异手术**：选择器分组里只摘掉死的那部分
 * （如 `.card-head, .dsh-card-header, .section-head` → 保留前两个别名），
 * 整条规则全死才删规则。共删 **24 条规则 / 122 行 / 2,144 字节**。
 *
 * ## 为什么要两个独立证据
 *
 * 静态「类名出现在源码里吗」会漏掉**运行时拼出来的类名**；本轮实测确认
 * 全仓只有两种动态拼类（`dir-${norm}`、`is-${tagType}`），均不涉及删掉的类名。
 * 另外用真实浏览器扫 25 条路由的 DOM：**20 个候选类名一个都没出现过**。
 *
 * ## 批 96：扩展到**页面/组件 scoped 样式**
 *
 * 批 95 的探针只查全局样式表。扩展后覆盖每个 `.vue` 的 `<style>` 块 ——
 * 825 个 scoped 类名里跑出 **1 个零引用**：`AboutPage` 的 `.ab-comp:last-child`。
 * 它是批 90 的遗留：当时把行本体并入 `.kv-row` 时，删规则用的正则是
 * `^\.ab-comp\s*\{`，**匹配不到带伪类的 `.ab-comp:last-child {`**；
 * 而批 90 的判据只拉黑了 `ab-comp"`（**带引号的模板用法**），漏掉 CSS 规则形态。
 * 「末行不封口」的行为批 90 已收进原件 `.kv-row:last-child`，故该规则是纯死代码。
 *
 * ## 批 96 之二：元素选择器
 *
 * 同类死代码还有**元素选择器**形态：`base.css` 的 `h1,h2,h3,h4,h5,h6` 分组里，
 * `h5`/`h6` 在**全仓模板 0 次**出现（`h1` 9 / `h2` 63 / `h3` 20 / `h4` 10），
 * 也没有各自的字号规则，更没有 `v-html` 在运行时生成标题 → 摘除。
 *
 * ⚠️ 这里**不能用 DOM 采样**做判据：`h4` 在默认渲染的 26 条路由里也是 0 个，
 * 但模板里有 10 处（都在条件渲染的分区/弹层里）。**静态模板扫描才是对的仪器**。
 * 实测其余 16 个元素选择器（body/html/p/button/input/…）都有 1~279 次使用，
 * 所以本判据**不需要任何白名单**。
 *
 * ## ⚠️ 维护须知
 *
 * 若将来用 `v-bind`/字符串拼接生成类名，本判据会**误报死代码**。
 * 届时请在 `DYNAMIC_PREFIXES` 里登记前缀，而不是删掉 CSS。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const GLOBAL_SHEETS = ['components.css', 'base.css', 'tokens.css', 'index.css'];

/** 动态拼类名的前缀（本项目实测只有这两个）。 */
export const DYNAMIC_PREFIXES = ['dir-', 'is-'];

/** 批 95 删掉的 20 个类名（回归锚点：不得回潮）。 */
export const DELETED = [
  'card-body', 'badge-brand',
  'badge-mono', 'btn-filled', 'btn-lg', 'btn-secondary', 'card-pad', 'divider', 'divider-v',
  'dot-down', 'dot-warn', 'input-prose', 'log-line', 'route-enter', 'section-body', 'section-desc',
  'section-head', 'stat', 'stat-foot', 'stat-label', 'stat-value', 'truncate-1',
];

function walk(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

/** 全局样式表里定义的类名 → 定义处。 */
export function definedClasses() {
  const map = new Map();
  for (const name of GLOBAL_SHEETS) {
    const css = readFileSync(path.join(SRC, 'styles', name), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    css.split('\n').forEach((line, i) => {
      for (const m of line.matchAll(/\.([A-Za-z_][-\w]*)/g)) {
        if (!map.has(m[1])) map.set(m[1], `${name}:${i + 1}`);
      }
    });
    // 多行选择器：把上一行未闭合的 `{` 之前的内容也算进来
    for (const m of css.matchAll(/([^{}]*)\{/g)) {
      for (const c of m[1].matchAll(/\.([A-Za-z_][-\w]*)/g)) {
        if (!map.has(c[1])) map.set(c[1], name);
      }
    }
  }
  return map;
}

/** 模板 / 脚本里出现过的词（去掉 <style> 块）。 */
export function usedWords() {
  const used = new Set();
  for (const f of walk(SRC)) {
    if (!/\.(vue|ts|js|html)$/.test(f)) continue;
    let t = readFileSync(f, 'utf8');
    if (f.endsWith('.vue')) t = t.replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
    for (const m of t.matchAll(/[A-Za-z_][-\w]*/g)) used.add(m[0]);
  }
  return used;
}

/** 由 `<Transition name="X">` 推出的运行时类名。 */
export function runtimeTransitionClasses() {
  const out = new Set();
  const suffixes = ['enter', 'enter-from', 'enter-to', 'enter-active', 'leave', 'leave-from', 'leave-to', 'leave-active', 'move'];
  for (const f of walk(SRC)) {
    if (!f.endsWith('.vue')) continue;
    const t = readFileSync(f, 'utf8');
    for (const m of t.matchAll(/<Transition(?:Group)?[^>]*\bname="([^"]+)"/g)) {
      for (const s of suffixes) out.add(`${m[1]}-${s}`);
    }
  }
  return out;
}

/** 各 .vue 的 `<style>` 块里定义的类名 → 定义处。 */
export function scopedClasses() {
  const map = new Map();
  for (const f of walk(SRC)) {
    if (!f.endsWith('.vue')) continue;
    const raw = readFileSync(f, 'utf8');
    const rel = path.relative(SRC, f);
    for (const m of raw.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
      const before = raw.slice(0, m.index).split('\n').length;
      const css = m[1].replace(/\/\*[\s\S]*?\*\//g, '');
      for (const r of css.matchAll(/([^{}]*)\{/g)) {
        const line = before + css.slice(0, r.index).split('\n').length;
        for (const c of r[1].matchAll(/\.([A-Za-z_][-\w]*)/g)) {
          if (!map.has(c[1])) map.set(c[1], `${rel}:${line}`);
        }
      }
    }
  }
  return map;
}

test('页面/组件 scoped 样式里也不得有零引用类名（批 96 扩展）', () => {
  const used = usedWords();
  const runtime = runtimeTransitionClasses();
  const dead = [];
  for (const [cls, where] of scopedClasses()) {
    if (used.has(cls)) continue;
    if (runtime.has(cls)) continue;
    if (DYNAMIC_PREFIXES.some((p) => cls.startsWith(p))) continue;
    dead.push(`.${cls}（定义于 ${where}）`);
  }
  assert.deepEqual(
    dead,
    [],
    '这些 scoped 类名全仓零引用 —— 是死 CSS（注意：伪类形态如 .x:hover / .x:last-child 也算定义）：\n  ' +
      dead.join('\n  '),
  );
});

/** 全局样式表里的元素选择器 → 定义它的样式表。 */
export function elementSelectors() {
  const map = new Map();
  for (const name of GLOBAL_SHEETS) {
    let css = readFileSync(path.join(SRC, 'styles', name), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    // 去掉 @keyframes 块：里面的 from/to/0% 不是元素选择器
    css = css.replace(/@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, '');
    for (const m of css.matchAll(/([^{}]+)\{/g)) {
      for (const part of m[1].split(',')) {
        // ⚠️ 标签名允许连字符：自定义元素（`my-el`）也是元素选择器。
        // 初版写的是 `[a-z][a-z0-9]*`，变异测试里「加一条 `zz-tag {}`」**没能翻红** —— 判据被放宽。
        const mm = part.trim().match(/^([a-z][a-z0-9-]*)(?:::?[a-z-]+(?:\([^)]*\))?)?$/);
        if (mm && !map.has(mm[1])) map.set(mm[1], name);
      }
    }
  }
  return map;
}

/** 模板 / index.html 里出现过的标签名 → 次数。 */
export function usedTags() {
  const counts = new Map();
  const bump = (k) => counts.set(k, (counts.get(k) || 0) + 1);
  for (const f of walk(SRC)) {
    if (!f.endsWith('.vue')) continue;
    const t = readFileSync(f, 'utf8');
    const m = /<template>([\s\S]*)<\/template>/.exec(t);
    if (!m) continue;
    for (const mm of m[1].matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)/g)) bump(mm[1].toLowerCase());
  }
  const idx = readFileSync(path.join(SRC, '..', 'index.html'), 'utf8');
  for (const mm of idx.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)/g)) bump(mm[1].toLowerCase());
  return counts;
}

test('全局样式表里的元素选择器，其标签必须真在模板里用过（批 96）', () => {
  const tags = usedTags();
  const dead = [];
  for (const [tag, sheet] of elementSelectors()) {
    if ((tags.get(tag) || 0) === 0) dead.push(`<${tag}>（定义于 ${sheet}）`);
  }
  assert.deepEqual(
    dead,
    [],
    `这些元素选择器对应的标签在全仓模板里 0 次出现 —— 是死 CSS：\n  ${dead.join('\n  ')}`,
  );
});

test('批 90 遗留的 .ab-comp 死规则不得回潮', () => {
  const scoped = scopedClasses();
  assert.ok(!scoped.has('ab-comp'), `.ab-comp 又出现在 scoped 样式里（${scoped.get('ab-comp')}）`);
  // ⚠️ 必须先剥注释：文件里那句「批 96 说明」本身就提到了 .ab-comp
  const src = readFileSync(path.join(SRC, 'views/admin/AboutPage.vue'), 'utf8')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(!/\.ab-comp[\s,:{]/.test(src), 'AboutPage 里又出现 .ab-comp 规则');
});

test('全局样式表里不得再有零引用的类名（死 CSS 探针）', () => {
  const defined = definedClasses();
  const used = usedWords();
  const runtime = runtimeTransitionClasses();
  const dead = [];
  for (const [cls, where] of defined) {
    if (used.has(cls)) continue;
    if (runtime.has(cls)) continue;
    if (DYNAMIC_PREFIXES.some((p) => cls.startsWith(p))) continue;
    dead.push(`.${cls}（定义于 ${where}）`);
  }
  assert.deepEqual(
    dead,
    [],
    `这些类名在全局样式表里定义了但全仓零引用 —— 是死 CSS，请删除（或登记为动态生成）：\n  ${dead.join('\n  ')}`,
  );
});

test('批 95 删除的 20 个类名不得回潮', () => {
  const defined = definedClasses();
  const back = DELETED.filter((c) => defined.has(c));
  assert.deepEqual(back, [], `已确认零引用并删除的类名又回来了：${back.map((c) => '.' + c).join(', ')}`);
});

test('删死类名时被摘掉的分组，其幸存别名必须完好', () => {
  const css = readFileSync(path.join(SRC, 'styles', 'components.css'), 'utf8');
  for (const sel of ['.card-head', '.dsh-card-header', '.card-sub', '.btn-ghost', '.dsh-log-entry', '.dsh-pill.brand']) {
    assert.ok(
      new RegExp(`\\${sel}\\s*[,{]`).test(css) || new RegExp(`\\${sel}[^\\w-]`).test(css),
      `幸存别名 ${sel} 不见了（摘除死选择器时误删）`,
    );
  }
  // 曾经和它们同组的死类名不得再出现
  for (const gone of ['.section-head', '.log-line', '.card-body', '.badge-brand']) {
    assert.ok(!new RegExp('\\' + gone + '[\\s,{]').test(css), `${gone} 回潮了`);
  }
});

test('判据自检：运行时过渡类与动态前缀的放行逻辑', () => {
  const runtime = runtimeTransitionClasses();
  assert.ok(runtime.has('fade-leave-active'), '应从 name="fade" 推出 fade-leave-active');
  assert.ok(runtime.has('pop-leave-to'), '应从 name="pop" 推出 pop-leave-to');
  assert.ok(!runtime.has('route-enter'), '没有 name="route"，不该推出 route-enter');
  // 动态前缀
  assert.equal(DYNAMIC_PREFIXES.some((p) => 'dir-up'.startsWith(p)), true);
  assert.equal(DYNAMIC_PREFIXES.some((p) => 'stat-value'.startsWith(p)), false);
});
