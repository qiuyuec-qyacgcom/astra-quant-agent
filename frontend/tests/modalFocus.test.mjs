/**
 * 模态焦点行为闸（批 42）。
 *
 * ## 守什么
 *
 * 一个声明了 `aria-modal="true"` 的浮层，必须真的具备模态行为：
 * Escape 可关、Tab 不逃到遮罩后的背景页、打开/关闭时焦点有交接、背景不滚。
 * 实测（批 42）`TrajectoryPanel.vue` 四条**全无** —— 它是全站唯一关不掉的模态，
 * 而 `BaseDialog` / `BaseDrawer` 各写了一份相同实现，谁都没发现第三处漏了。
 *
 * 判据（静态、不跑浏览器）：文件里出现 `aria-modal` ⇒ 必须
 *   - 调用 `useModalFocus(`，或
 *   - 使用 `BaseDialog` / `BaseDrawer`（两者内部已接同一份实现）。
 *
 * ## 为什么不是"数行数"那种闸
 *
 * 断言的是**接线关系**，不是实现细节：将来把 `useModalFocus` 换成别的等价方案，
 * 只要该方案被模态引用，闸就仍然通过；反之，新加一个"自己画遮罩"的模态会立刻红。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

/**
 * 去掉注释再判接线关系。
 *
 * 第一版直接对原文匹配 `/\bBaseDrawer\b/`，结果**变异测试没红** —— 因为
 * TrajectoryPanel 的注释里就写着"与 BaseDialog / BaseDrawer 同一份实现"，
 * 光提名字就满足了判据。**闸必须匹配真正的接线，不能匹配提到过的名字。**
 */
function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

/** 声明了模态语义的文件 → 它必须接入焦点管理 */
function modalsWithoutFocus() {
  const bad = [];
  for (const file of vueFiles(SRC)) {
    const raw = readFileSync(file, 'utf8');
    if (!/aria-modal/.test(stripComments(raw))) continue;
    const text = stripComments(raw);
    const wired =
      /useModalFocus\s*\(/.test(text) ||      // 直接接组合式
      /<BaseDialog\b/.test(text) ||           // 模板里真的用了基础组件
      /<BaseDrawer\b/.test(text);
    if (!wired) bad.push(path.relative(SRC, file));
  }
  return bad;
}

test('声明 aria-modal 的浮层必须接入焦点管理（Escape / Tab 环绕 / 滚动锁）', () => {
  const bad = modalsWithoutFocus();
  assert.deepEqual(
    bad,
    [],
    `这些文件声明了 aria-modal 却没有焦点管理（Escape 关不掉、Tab 会走到背景页）：\n  ${bad.join('\n  ')}\n` +
      '请接入 useModalFocus（或改用 BaseDialog / BaseDrawer）。',
  );
});

test('闸自检：接线判据能真的命中', () => {
  const wired = (t) =>
    /useModalFocus\s*\(/.test(t) || /<BaseDialog\b/.test(t) || /<BaseDrawer\b/.test(t);
  assert.equal(wired('<aside aria-modal="true">'), false, '未接线的模态应判失败');
  assert.equal(wired("// 与 BaseDialog / BaseDrawer 同一份实现"), false, '只提名字不算接线');
  assert.equal(wired("import { useModalFocus } from './useModalFocus'\nuseModalFocus(panel, cb)"), true);
  assert.equal(wired('<BaseDrawer :open="o" />'), true);
});
