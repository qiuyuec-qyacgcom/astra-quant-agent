/**
 * 图标按钮可访问名与提示信息守卫闸（批 48）。
 *
 * ## 守什么
 *
 * 仅包含图标（如 <X />, <Menu />）的按钮如果既没有文字、又没有 aria-label/title，
 * 会导致两类体验严重受损：
 * 1. 读屏器用户遭遇「无名按钮」（只会念出 "button"），完全不知道点击后是关闭、展开还是切换；
 * 2. 鼠标悬浮时没有原生 Tooltip 气泡提示。
 *
 * 规则：模板中所有无文本内容的图标按钮，必须显式配齐 `aria-label` / `:aria-label` 或 `title` / `:title`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
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

const stripComments = (t) => t.replace(/<!--[\s\S]*?-->/g, '');

test('所有纯图标按钮必须声明 aria-label 或 title', () => {
  const badButtons = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));

    for (const m of text.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
      const attrs = m[1];
      const inner = m[2].trim();

      const hasLabel = ['aria-label', ':aria-label', 'title', ':title'].some((k) =>
        attrs.includes(k)
      );
      if (hasLabel) continue;

      // 如果有 slot 则文本来自运行时注入
      if (inner.includes('<slot')) continue;

      // 检查剥除 HTML 标签后是否仍有文字或插值
      const innerNoTags = inner.replace(/<[^>]*>/g, '').trim();
      if (!innerNoTags) {
        const line = text.slice(0, m.index).split('\n').length;
        badButtons.append?.(`${rel}:${line}`) ?? badButtons.push(`${rel}:${line} 纯图标按钮缺少 label/title`);
      }
    }
  }

  assert.deepEqual(badButtons, [], `发现未命名的纯图标按钮：\n  ${badButtons.join('\n  ')}`);
});

test('闸自检：能准确命中无名图标按钮并放行合规按钮', () => {
  const badBtn = '<button class="icon-btn"><X :size="14" /></button>';
  const goodBtn1 = '<button class="icon-btn" :title="t(\'common.close\')"><X :size="14" /></button>';
  const goodBtn2 = '<button class="icon-btn" aria-label="Close"><X :size="14" /></button>';
  const goodTextBtn = '<button class="btn"><Plus /><span>新建</span></button>';

  const isBad = (html) => {
    const m = html.match(/<button\b([^>]*)>([\s\S]*?)<\/button>/);
    if (!m) return false;
    const attrs = m[1];
    const inner = m[2];
    const hasLabel = ['aria-label', ':aria-label', 'title', ':title'].some((k) => attrs.includes(k));
    if (hasLabel) return false;
    if (inner.includes('<slot')) return false;
    return !inner.replace(/<[^>]*>/g, '').trim();
  };

  assert.equal(isBad(badBtn), true, '应拦截无名图标按钮');
  assert.equal(isBad(goodBtn1), false, '应放行带 title 按钮');
  assert.equal(isBad(goodBtn2), false, '应放行带 aria-label 按钮');
  assert.equal(isBad(goodTextBtn), false, '应放行带文字按钮');
});
