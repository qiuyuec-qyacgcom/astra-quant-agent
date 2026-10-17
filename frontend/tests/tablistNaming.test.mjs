/**
 * `role="tablist"` 必须**有可访问名**的守卫闸（批 110）。
 *
 * ## 实测出来的缺口
 *
 * 25 个路由 + 展开偏好设置后共 **17 条 tablist**，其中 **4 条没有名字**：
 * `/` 与 `/trading` 各两条 —— 周期分段（15分/1时/…）与「活动持仓 / 在途挂单」分段。
 * 读屏器只会念"标签列表"，用户不知道这几个分段在筛选什么。
 *
 * `BaseSegmented` 的 `label` prop 注释（批 44）**正是为了这件事**而存在，
 * 只是当时写成了可选，于是漏传不会报错、也没人发现。
 *
 * ## 修法：把不变式交给编译器
 *
 * `label` 由可选改**必填**（`BaseSegmented` 与 `BaseTabs` 都是），
 * 漏传会在 `vue-tsc` 阶段直接失败。改完 `vue-tsc` 一次通过 =
 * **所有调用点都已传**（这是比 grep 更强的证据）。
 * 实测复测：25 路由 + 偏好设置的 tablist **21 条，21 条有名，0 条无名**。
 *
 * 本闸再补一层：万一有人绕过类型检查（比如在模板里动态 `v-bind`），
 * 静态扫描也能发现漏传。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const COMPONENTS = ['BaseSegmented', 'BaseTabs'];

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

/**
 * 找出「自闭合标签」形态的组件调用，判断标签内是否给了组名。
 * 只认顶层属性 `label=` / `:label=` —— `:options="[{ label: 'x' }]"` 里的
 * `label:` 带的是冒号不是等号，不算传了组名（这正是需要区分的两种情况）。
 * 导出以便自检。
 */
export function callSitesMissingLabel(source, component) {
  const out = [];
  for (const m of source.matchAll(new RegExp('<' + component + '[\\s\\S]*?/>', 'g'))) {
    if (!/(^|\s):?label=/.test(m[0])) out.push(m[0].replace(/\s+/g, ' ').slice(0, 90));
  }
  return out;
}

test('BaseSegmented / BaseTabs 的 label 必须是必填 prop（漏传要编译失败）', () => {
  for (const c of COMPONENTS) {
    const src = readFileSync(path.join(SRC, 'components/base', c + '.vue'), 'utf8');
    assert.match(src, /(^|\s)label:\s*string/, `${c} 的 label 又不是必填了 —— 漏传会退回"无名 tablist"`);
    // ⚠️ 只查**顶格**声明：`options: { … label?: string … }[]` 里的那个 label 是
    //    每个选项自己的文字（本来就该可选），不能拿它当"prop 被改回可选"的证据。
    assert.ok(!/^\s*label\?:\s*string/m.test(src), `${c} 的 label 被改回可选了`);
  }
});

test('所有 BaseSegmented / BaseTabs 调用点都必须传 label（静态兜底）', () => {
  const bad = [];
  for (const f of vueFiles(SRC)) {
    const src = readFileSync(f, 'utf8');
    const rel = path.relative(path.resolve(SRC, '..'), f).split(path.sep).join('/');
    for (const c of COMPONENTS) {
      for (const tag of callSitesMissingLabel(src, c)) bad.push(rel + '  <' + c + ' … ' + tag);
    }
  }
  assert.deepEqual(
    bad,
    [],
    '这些 tablist 调用点没有传组名（读屏器只会念"标签列表"）：\n  ' + bad.join('\n  '),
  );
});

test('判据自检：漏传要能发现，`:options` 里的 label 不能算数', () => {
  // ① 真漏传
  assert.equal(callSitesMissingLabel('<BaseSegmented v-model="x" :options="opts" />', 'BaseSegmented').length, 1);
  // ② `:options` 里出现 label: 不算传了组名（最容易骗过判据的写法）
  const tricky = '<BaseSegmented :options="[{ value: \'a\', label: t(\'x\') }]" />';
  assert.equal(callSitesMissingLabel(tricky, 'BaseSegmented').length, 1, ':options 里的 label 被误当成组名了');
  // ③ 传了 :label
  assert.equal(callSitesMissingLabel('<BaseSegmented :label="t(\'a\')" :options="opts" />', 'BaseSegmented').length, 0);
  // ④ 传了静态 label
  assert.equal(callSitesMissingLabel('<BaseTabs label="分区" :items="items" />', 'BaseTabs').length, 0);
  // ⑤ 多行标签也要覆盖
  const multiline = '<BaseSegmented\n  v-model="x"\n  :options="opts"\n/>';
  assert.equal(callSitesMissingLabel(multiline, 'BaseSegmented').length, 1);
});

test('新补的组名两种语言都要有（en 不得含中文）', () => {
  const zh = readFileSync(path.join(SRC, 'locales/zh/dash/matrix.ts'), 'utf8');
  const en = readFileSync(path.join(SRC, 'locales/en/dash/matrix.ts'), 'utf8');
  for (const [name, src] of [['zh', zh], ['en', en]]) {
    assert.match(src, /positionsOrders:\s*\{[\s\S]{0,120}?tabsAria:\s*'[^']+'/, `${name} 缺少 positionsOrders.tabsAria`);
  }
  const enVal = /tabsAria:\s*'([^']+)'/.exec(/positionsOrders:\s*\{[\s\S]{0,140}?\}/.exec(en)[0])[1];
  assert.ok(!/[\u4e00-\u9fff]/.test(enVal), 'en 的组名里混进了中文：' + enVal);
});
