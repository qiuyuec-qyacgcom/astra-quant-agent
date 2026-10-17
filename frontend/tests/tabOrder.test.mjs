/**
 * Tab 顺序守卫闸（批 111）。
 *
 * ## 实测出来的缺陷
 *
 * 键盘用户在某页按第一次 Tab，落点不是「跳到主内容」，而是一块
 * **没有名字、没有焦点环、什么也不做**的画布（实测 895×379、`outline: none`）。
 * 根因在第三方图表库：klinecharts 的 dist 里有一行
 * `this._chartContainer.tabIndex = 1` —— **正数 tabindex 会插队到所有
 * `tabindex="0"` 之前**，包括各页顶部的跳转链接。
 *
 * 实测（修前，`/` 与 `/trading`）：
 * ```
 * 第一次 Tab → DIV(无类无角色无名)  ❌  跳到主内容 变成第二次 Tab 才到
 * ```
 * 修后（5 个路由）：
 * ```
 * 正数 tabindex 个数 0 ／ 第一次 Tab → A.skip-link（可见）
 * Enter → #main-content ／ 再 Tab → 仍在主内容内
 * ```
 *
 * ## 为什么是「摘掉」而不是「降级成 0」
 *
 * 库的键盘能力挂在 **`document`** 上（`_boundKeyBoardDownEvent` 是 document 级监听，
 * 只对 input/textarea/contenteditable 让路），**不依赖容器获得焦点**；
 * 容器也不可滚动（`overflow-y: hidden`、`scrollHeight == clientHeight`）；
 * 本仓对「内部没有可聚焦元素的纯展示区」的既定做法同样是不进 Tab 顺序。
 * 降级成 0 只会让页面中段多出一个无名停靠点。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { demotePositiveTabIndex } from '../src/utils/tabOrder.ts';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

/**
 * 剥掉注释再扫描。⚠️ 本文件的**文档注释里就写着** `tabindex="1"` / `tabIndex = 1`
 * 作为反面例子；不剥的话一是会把自家文档当违规，二是——
 * **把代码注释掉也算"还在"**（变异 M65 当场抓到的：注掉归一化调用后判据仍是绿的）。
 */
const stripComments = (s) =>
  s
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|\s)\/\/[^\n]*/g, ' ');

/** 造一棵假的元素树：只需要 `tabIndex` 与 `querySelectorAll`。 */
function fakeTree(rootTab, childTabs) {
  const children = childTabs.map((t) => ({ tabIndex: t }));
  return { tabIndex: rootTab, querySelectorAll: () => children, _children: children };
}

test('正数 tabIndex 一律改成 -1（根元素与后代都算）', () => {
  const tree = fakeTree(1, [0, -1, 1, 3, 2]);
  assert.equal(demotePositiveTabIndex(tree), 4, '改动计数不对（根 1 个 + 后代 3 个）');
  assert.equal(tree.tabIndex, -1);
  assert.deepEqual(tree._children.map((c) => c.tabIndex), [0, -1, -1, -1, -1], '0/-1 被动了或被漏掉了');
});

test('0 与 -1 必须原样保留（别把正常/刻意排除的元素也改掉）', () => {
  const tree = fakeTree(0, [0, -1]);
  assert.equal(demotePositiveTabIndex(tree), 0);
  assert.equal(tree.tabIndex, 0);
  assert.deepEqual(tree._children.map((c) => c.tabIndex), [0, -1]);
});

test('空/未挂载的容器不能抛错（图表还没初始化时会被调用）', () => {
  assert.equal(demotePositiveTabIndex(null), 0);
  assert.equal(demotePositiveTabIndex(undefined), 0);
  const empty = fakeTree(0, []);
  assert.equal(demotePositiveTabIndex(empty), 0);
});

test('幂等：重复调用第二次不再改动', () => {
  const tree = fakeTree(1, [2]);
  assert.equal(demotePositiveTabIndex(tree), 2);
  assert.equal(demotePositiveTabIndex(tree), 0, '第二次调用又改动了（说明不是幂等）');
});

test('回归：全仓源码不得再引入正数 tabindex（这次缺陷的形态）', () => {
  // ⚠️ 必须先剥注释：本次修复的**文档注释里就写着** `tabindex="1"` / `tabIndex = 1`
  //    作为反面例子，不剥会把自家文档当成违规（本项目反复踩过的坑）。
  const bad = [];
  const walk = (dir) => {
    for (const n of readdirSync(dir)) {
      const p = path.join(dir, n);
      if (statSync(p).isDirectory()) { walk(p); continue; }
      if (!/\.(vue|ts)$/.test(n)) continue;
      const rel = path.relative(path.resolve(SRC, '..'), p).split(path.sep).join('/');
      const src = stripComments(readFileSync(p, 'utf8'));
      for (const m of src.matchAll(/tabindex="([1-9]\d*)"/g)) bad.push(rel + '  tabindex="' + m[1] + '"');
      for (const m of src.matchAll(/tabIndex\s*=\s*([1-9]\d*)\b/g)) bad.push(rel + '  tabIndex = ' + m[1]);
    }
  };
  walk(SRC);
  assert.deepEqual(bad, [], '正数 tabindex 会插队到「跳到主内容」之前，禁止新增：\n  ' + bad.join('\n  '));
});

test('判据自检：剥注释后仍能抓到真正的正数 tabindex', () => {
  const hits = (s) => [...stripComments(s).matchAll(/tabindex="([1-9]\d*)"/g)].length;
  assert.equal(hits('// 反例：tabindex="1" 会插队'), 0, '注释里的例子被当成了违规');
  assert.equal(hits('<div tabindex="1">真违规</div>'), 1, '真正的违规没被抓到');
  assert.equal(hits('tabindex="0"'), 0, 'tabindex="0" 被误报');
  assert.equal(hits('tabindex="-1"'), 0, 'tabindex="-1" 被误报');
});

test('图表组件必须在初始化之后归一化，且用的是抽出来的工具', () => {
  const src = stripComments(readFileSync(path.join(SRC, 'components/dashboard/ChartWorkstation.vue'), 'utf8'));
  assert.match(src, /import \{ demotePositiveTabIndex \} from '\.\.\/\.\.\/utils\/tabOrder'/, '没有引用 tabOrder 工具');
  const initAt = src.indexOf('initKLineChart(chartContainer.value');
  const callAt = src.indexOf('demotePositiveTabIndex(chartContainer.value)');
  assert.ok(initAt > 0 && callAt > 0, '初始化或归一化调用不见了');
  assert.ok(callAt > initAt, '归一化必须在 initKLineChart 之后（要等库把容器建出来）');
});
