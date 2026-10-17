/**
 * 对话框与选项卡 ARIA 语义守卫闸（批 50）。
 *
 * ## 守什么
 *
 * 1. **Dialog / Drawer 无障碍标题（WCAG 1.3.1 & 4.1.2）**：
 *    `role="dialog"` 必须具备可访问名称。`BaseDialog.vue` 与 `BaseDrawer.vue`
 *    必须通过 `:aria-labelledby` 绑定到标题元素（由 `useId()` 生成唯一 ID），
 *    读屏器在唤起模态弹窗/抽屉时才能准确播报弹窗标题。
 *
 * 2. **Tab 与 Tablist 语义闭环（WAI-ARIA Tabs Design Pattern）**：
 *    ① 每一个声明 `role="tab"` 的按钮，其祖先容器必须声明 `role="tablist"`（栈式感知）；
 *    ② 任何声明 `role="tablist"` 的容器，必须声明 `aria-label` 或 `aria-labelledby`。
 *    （此前 DecisionsPage、PromptStudioPage、ProviderDetailView、BaseTabs 均遗漏）
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const VOID_ELEMENTS = new Set(['input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'area', 'col', 'base', 'embed', 'param', 'track', 'wbr']);
const TAG_RE = /<(\/?)([a-zA-Z][\w.-]*)((?:"[^"]*"|[^>"])*)>/g;

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

const stripComments = (t) => t.replace(/<!--[\s\S]*?-->/g, '');

test('BaseDialog 与 BaseDrawer 必须绑定 :aria-labelledby 到标题', () => {
  const dialogText = readFileSync(path.join(SRC, 'components/base/BaseDialog.vue'), 'utf8');
  assert.match(dialogText, /:aria-labelledby="[^"]*titleId[^"]*"/, 'BaseDialog 缺失 :aria-labelledby 绑定');
  assert.match(dialogText, /:id="titleId"/, 'BaseDialog 标题缺失 :id 绑定');

  const drawerText = readFileSync(path.join(SRC, 'components/base/BaseDrawer.vue'), 'utf8');
  assert.match(drawerText, /:aria-labelledby="[^"]*titleId[^"]*"/, 'BaseDrawer 缺失 :aria-labelledby 绑定');
  assert.match(drawerText, /:id="titleId"/, 'BaseDrawer 标题缺失 :id 绑定');
});

test('每一个 role="tab" 必须位于带有 aria-label 的 role="tablist" 容器内', () => {
  const missingTablist = [];
  const unlabelledTablist = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const content = stripComments(readFileSync(file, 'utf8'));
    const templateMatch = content.match(/<template>([\s\S]*)<\/template>/);
    if (!templateMatch) continue;
    const body = templateMatch[1];

    const stack = [];
    for (const m of body.matchAll(TAG_RE)) {
      const closing = m[1];
      const name = m[2].toLowerCase();
      const attrs = m[3];

      if (closing) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].name === name) {
            stack.length = i;
            break;
          }
        }
        continue;
      }

      const selfClose = attrs.trimEnd().endsWith('/');

      if (attrs.includes('role="tab"')) {
        const inTablist = stack.some((s) => s.attrs.includes('role="tablist"'));
        if (!inTablist) {
          const line = content.slice(0, templateMatch.index).split('\n').length + body.slice(0, m.index).split('\n').length;
          missingTablist.push(`${rel}:${line} 中的 role="tab" 缺少 role="tablist" 祖先容器`);
        }
      }

      if (attrs.includes('role="tablist"')) {
        const hasLabel = /:?aria-label=/.test(attrs) || /:?aria-labelledby=/.test(attrs);
        if (!hasLabel) {
          const line = content.slice(0, templateMatch.index).split('\n').length + body.slice(0, m.index).split('\n').length;
          unlabelledTablist.push(`${rel}:${line} 中的 role="tablist" 缺少 aria-label 描述`);
        }
      }

      if (!selfClose && !VOID_ELEMENTS.has(name)) {
        stack.push({ name, attrs });
      }
    }
  }

  assert.deepEqual(missingTablist, [], `发现孤立的 role="tab"：\n  ${missingTablist.join('\n  ')}`);
  assert.deepEqual(unlabelledTablist, [], `发现未命名的 role="tablist"：\n  ${unlabelledTablist.join('\n  ')}`);
});

test('闸自检：能准确识别缺失 tablist 或未命名 tablist', () => {
  const badTabs = '<button role="tab">Tab 1</button>';
  const badTablist = '<div role="tablist"><button role="tab">Tab 1</button></div>';
  const goodTablist = '<div role="tablist" aria-label="Tabs"><button role="tab">Tab 1</button></div>';

  const check = (html) => {
    let missing = false;
    let unlabelled = false;
    const stack = [];
    for (const m of html.matchAll(TAG_RE)) {
      const closing = m[1];
      const name = m[2].toLowerCase();
      const attrs = m[3];
      if (closing) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].name === name) { stack.length = i; break; }
        }
        continue;
      }
      if (attrs.includes('role="tab"') && !stack.some((s) => s.attrs.includes('role="tablist"'))) {
        missing = true;
      }
      if (attrs.includes('role="tablist"') && !/:?aria-label=/.test(attrs)) {
        unlabelled = true;
      }
      if (!attrs.trimEnd().endsWith('/') && !VOID_ELEMENTS.has(name)) {
        stack.push({ name, attrs });
      }
    }
    return { missing, unlabelled };
  };

  assert.equal(check(badTabs).missing, true, '孤立 tab 应被拦截');
  assert.equal(check(goodTablist).missing, false, '合规 tablist 应放行');
  assert.equal(check(badTablist).unlabelled, true, '无 label 的 tablist 应被拦截');
  assert.equal(check(goodTablist).unlabelled, false, '有 label 的 tablist 应放行');
});
