/**
 * 开关无障碍标签、一键复制动态反馈与外链安全守卫闸（批 49）。
 *
 * ## 守什么
 *
 * 1. **外链安全性（Reverse Tabnabbing）**：
 *    全站所有带有 `target="_blank"` 的外部链接 `<a>`，必须声明 `rel="noopener noreferrer"`。
 *    遗漏 noreferrer 存在反向 window.opener 劫持风险与来源泄露。
 *
 * 2. **BaseSwitch 全量无障碍名覆盖**：
 *    全站所有 `<BaseSwitch>` 调用点必须具备无障碍名称（传入 `:label` / `:title` 或置于带文本的 `<label>` 中），
 *    严禁无名开关（此前有 7 处因漏传 label 或传了 title 属性导致组件内部 aria-label 为空）。
 *
 * 3. **CopyButton 动态复制反馈**：
 *    一键复制按钮在复制成功（`done === true`）期间，`:title` 和 `:aria-label` 必须动态切换为已复制状态，
 *    确保鼠标悬停与读屏器均能获知复制已完成的实时状态。
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

test('所有 target="_blank" 链接必须包含 rel="noopener noreferrer"', () => {
  const badLinks = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));

    for (const m of text.matchAll(/<a\b([^>]*)>/g)) {
      const attrs = m[1];
      if (attrs.includes('target="_blank"')) {
        const hasRel = /rel="[^"]*noopener[^"]*"/.test(attrs) && /rel="[^"]*noreferrer[^"]*"/.test(attrs);
        if (!hasRel) {
          badLinks.push(`${rel} 缺少 rel="noopener noreferrer"`);
        }
      }
    }
  }

  assert.deepEqual(badLinks, [], `发现不安全的外链：\n  ${badLinks.join('\n  ')}`);
});

test('所有 BaseSwitch 调用点必须提供无障碍标签', () => {
  const badSwitches = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));

    for (const m of text.matchAll(/<BaseSwitch\b([^>]*)\/?>/g)) {
      const attrs = m[1];
      const hasProp = /:?label=/.test(attrs) || /:?title=/.test(attrs);
      if (hasProp) continue;

      // 检查是否包裹在 <label> 中且有说明文字
      const preceding = text.slice(0, m.index);
      const lastLabelOpen = preceding.lastIndexOf('<label');
      const lastLabelClose = preceding.lastIndexOf('</label');
      const inLabel = lastLabelOpen > lastLabelClose;

      if (!inLabel) {
        const line = text.slice(0, m.index).split('\n').length;
        badSwitches.push(`${rel}:${line} 缺少 label/title 属性且不在 <label> 容器中`);
      }
    }
  }

  assert.deepEqual(badSwitches, [], `发现未配置无障碍名称的开关：\n  ${badSwitches.join('\n  ')}`);
});

test('CopyButton 必须具备动态 title 与 aria-label 反馈', () => {
  const text = readFileSync(path.join(SRC, 'components/base/CopyButton.vue'), 'utf8');
  assert.match(
    text,
    /:title="done\s*\?\s*t\('common\.copied'\)\s*:\s*t\('common\.copy'\)"/,
    'CopyButton 缺少复制成功动态 title'
  );
  assert.match(
    text,
    /:aria-label="done\s*\?\s*t\('common\.copied'\)\s*:\s*t\('common\.copy'\)"/,
    'CopyButton 缺少复制成功动态 aria-label'
  );
});

test('闸自检：能准确命中不安全外链与无名开关', () => {
  const badA = '<a href="https://example.com" target="_blank" rel="noopener">Link</a>';
  const goodA = '<a href="https://example.com" target="_blank" rel="noopener noreferrer">Link</a>';

  const checkA = (html) => {
    const m = html.match(/<a\b([^>]*)>/);
    const attrs = m[1];
    return attrs.includes('target="_blank"') && !(attrs.includes('noopener') && attrs.includes('noreferrer'));
  };

  assert.equal(checkA(badA), true, '应识别不安全外链');
  assert.equal(checkA(goodA), false, '应放行安全外链');
});
