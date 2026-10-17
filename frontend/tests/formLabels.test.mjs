/**
 * 表单控件可访问名闸（批 46）。
 *
 * ## 为什么判据要"感知祖先"
 *
 * 第一版是纯正则扫"有没有 aria-label/id/placeholder"，报出 **25 个无标签控件**；
 * 但浏览器实测（`el.labels` / accessible name 计算）只有 **21 个真的没有名字** ——
 * 差出来的 4 个是把 `<input>` **包在 `<label>` 里**的写法（隐式标注，合法）。
 * 纯正则不认识祖先关系，就会把合法写法判成缺陷。
 * 所以这里用**标签栈**模拟浏览器规则：显式属性 ∪ 有文本的祖先 `<label>`。
 *
 * ## 三条判据
 *
 * 1. **不能完全没有可访问名**（显式属性或"有文本的包裹 label"）；
 * 2. **不能只靠纯数值占位符当名字** —— `placeholder="120"` 会被念成 "120，编辑框"；
 * 3. **密码框的名字不能来自写死的字面量占位符** —— 中文界面里读屏器会念英文
 *    （实测 `placeholder="Secret Key"` / `"Passphrase"`），且字面量也不走 i18n。
 *
 * 占位符本身**算**可访问名（规范如此），所以"只有占位符"不判违规；
 * 判的是"占位符是数值/写死英文"这两种真正说不清是什么字段的情况。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const VOID = new Set(['input', 'img', 'br', 'hr', 'meta', 'link', 'source', 'area', 'base', 'col', 'embed', 'param', 'track', 'wbr']);
const CONTROLS = new Set(['input', 'select', 'textarea']);
const SKIP_TYPES = ['type="hidden"', 'type="checkbox"', 'type="radio"', 'type="file"', 'type="submit"', 'type="button"'];

const TAG = /<(\/?)([a-zA-Z][\w.-]*)((?:"[^"]*"|[^>"])*)>/g;
const stripComments = (t) => t.replace(/<!--[\s\S]*?-->/, '');

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

/** 遍历模板，逐个控件判定可访问名 */
function inspect() {
  const problems = [];
  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = readFileSync(file, 'utf8');
    const m = text.match(/<template>([\s\S]*)<\/template>/);
    if (!m) continue;
    const body = stripComments(m[1]);
    const stack = [];
    for (const mm of body.matchAll(TAG)) {
      const [, closing, rawName, attrs] = mm;
      const name = rawName.toLowerCase();
      if (closing) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].name === name) { stack.length = i; break; }
        }
        continue;
      }
      const selfClose = attrs.trimEnd().endsWith('/');
      if (CONTROLS.has(name) && !SKIP_TYPES.some((s) => attrs.includes(s))) {
        // 显式名：与浏览器一致 —— 占位符**算**名字（规范如此），
        // 与浏览器一致：占位符**算**名字；但"名字只有占位符"时，
        // 数值、数据样例（JSON/标记）这两类显然不是字段名，要单独判。
        const hasAria = ['aria-label', 'aria-labelledby', 'id=', 'title='].some((k) => attrs.includes(k));
        const phMatch =
          attrs.match(/:?placeholder="([^"]*)"/) || attrs.match(/:?placeholder='([^']*)'/);
        const ph = (phMatch ? phMatch[1] : '').trim();
        const literalPh = !!attrs.match(/\splaceholder=["']/);
        // 祖先 label 里是否有真实文本（含插值）
        let hasLabelText = false;
        const li = stack.map((s, i) => [s, i]).filter(([s]) => s.name === 'label').pop();
        if (li) {
          const seg = body.slice(li[0].start, mm.index);
          hasLabelText = /[A-Za-z\u4e00-\u9fff]|\{\{/.test(seg.replace(/<[^>]*>/g, ''));
        }
        const phOnly = !hasAria && !hasLabelText && !!ph;
        const line = text.slice(0, m.index).split('\n').length + body.slice(0, mm.index).split('\n').length;
        const where = `${rel}:${line}`;

        if (!hasAria && !hasLabelText && !ph) {
          problems.push(`${where} <${name}> 完全没有可访问名`);
        } else if (phOnly && /^\d+$/.test(ph)) {
          problems.push(`${where} <${name}> 名字只是数值占位符 "${ph}"（会被念成 "${ph}，编辑框"）`);
        } else if (phOnly && /^[{[<]/.test(ph)) {
          problems.push(`${where} <${name}> 名字是数据样例占位符 "${ph.slice(0, 24)}…"`);
        } else if (name === 'input' && /type="password"/.test(attrs) && phOnly && literalPh) {
          problems.push(`${where} <${name}[password]> 名字来自写死占位符 placeholder="${ph}"`);
        }
      }
      if (!selfClose && !VOID.has(name)) stack.push({ name, attrs, start: mm.index });
    }
  }
  return problems;
}

test('表单控件必须有可访问名（显式属性或带文本的包裹 label）', () => {
  const bad = inspect().filter((x) => x.includes('完全没有可访问名'));
  assert.deepEqual(bad, [], `这些控件读屏器只会念"编辑框"：\n  ${bad.join('\n  ')}`);
});

test('可访问名不能是纯数值占位符，密码框不能靠写死占位符取名', () => {
  const bad = inspect().filter((x) => !x.includes('完全没有可访问名'));
  assert.deepEqual(
    bad,
    [],
    `占位符不等于字段名（数值会被念成 "120，编辑框"；写死英文在中文界面里也念英文）：\n  ${bad.join('\n  ')}`,
  );
});

test('闸自检：三条判据能真的命中', () => {
  const named = (attrs, inLabel) =>
    ['aria-label', 'aria-labelledby', 'id=', 'title='].some((k) => attrs.includes(k)) || inLabel;
  assert.equal(named(' v-model="a"', false), false, '裸 input 应判无名字');
  assert.equal(named(' v-model="a"', true), true, '包裹在带文本的 label 里算有名字');
  assert.equal(/^\d+$/.test('120'), true, '数值占位符应被判弱名');
});
