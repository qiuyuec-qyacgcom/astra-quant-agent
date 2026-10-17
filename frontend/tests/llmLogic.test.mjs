/**
 * `src/composables/llmLogic.ts` 行为契约
 * （结构优化阶段 4·B3 第五十七刀）。
 *
 * ## 抽了什么、为什么这么抽
 *
 * `useLlmConfig.ts`（686 行）的 docstring 提过"按 provider / model / failover
 * 三域再拆"。实测后判断**不能那样拆**：那三域的动作函数都直接读写同一批 `ref`，
 * 拆成独立 composable 会各建一份新状态 —— `views/admin/llm/injection.ts`
 * 的注释已解释过该陷阱（"看起来能跑、实际全错"）。
 *
 * 故本刀只抽**真正无状态**的部分：输入进、值出，不碰任何 `ref`。
 *
 * ## 最大收益：消掉一处逐字重复
 *
 * `importRemoteModel` 与 `importAllFilteredRemoteModels` 里各写了一份
 * **完全相同**的模型 payload 字面量（11 个字段的回落链，实测字节一致）。
 * 现统一到 `buildRemoteModelPayload()`。
 *
 * ## 运行
 *     node --experimental-strip-types tests/llmLogic.test.mjs
 */
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const M = await import(pathToFileURL(path.resolve('src/composables/llmLogic.ts')).href);

let pass = 0, fail = 0;
function check(name, cond, extra = '') {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  → ' + extra : '')); }
}
const eq = (name, got, want) =>
  check(name, JSON.stringify(got) === JSON.stringify(want),
        'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));

// ------------------------------------------------------------ providerIdFromName
console.log('providerIdFromName:');
eq('小写化', M.providerIdFromName('ABC'), 'abc');
eq('trim', M.providerIdFromName('  x  '), 'x');
eq('非法字符→下划线', M.providerIdFromName('My Provider!'), 'my_provider_');
eq('空格与中文都换下划线', M.providerIdFromName('a b中'), 'a_b_');
eq('保留 - 与 _', M.providerIdFromName('a-b_c'), 'a-b_c');
eq('保留数字', M.providerIdFromName('p1'), 'p1');
eq('空串', M.providerIdFromName(''), '');
eq('null 不崩', M.providerIdFromName(null), '');
eq('undefined 不崩', M.providerIdFromName(undefined), '');
eq('连续非法字符合并', M.providerIdFromName('a!!!b'), 'a___b');

// ------------------------------------------------------------ supportsExtremeEffort
console.log('supportsExtremeEffort:');
for (const id of ['gpt-6', 'gpt-5-codex', 'o3-mini', 'o4', 'ultra-x', 'xmax', 'GPT-5']) {
  check(`${id} → true`, M.supportsExtremeEffort(id) === true);
}
for (const id of ['claude-sonnet-4', 'deepseek-v3', 'gpt-4o', '']) {
  check(`${JSON.stringify(id)} → false`, M.supportsExtremeEffort(id) === false);
}
check('null → false', M.supportsExtremeEffort(null) === false);
check('undefined → false', M.supportsExtremeEffort(undefined) === false);

// ------------------------------------------------------------ effortOptions
console.log('effortOptions:');
{
  const labelOf = (k) => 'L:' + k;
  const plain = M.effortOptions('claude-x', labelOf);
  eq('普通模型：6 档且顺序与原实现一致',
     plain.map(o => o.value), ['high', 'medium', 'low', 'minimal', 'none', 'auto']);
  eq('label 走注入的取值函数', plain[0].label, 'L:admin.llm.effortHigh');
  const ext = M.effortOptions('gpt-5', labelOf);
  eq('旗舰模型：max/xhigh 插到**最前**（unshift 不是 push）',
     ext.map(o => o.value).slice(0, 2), ['max', 'xhigh']);
  eq('旗舰模型共 8 档', ext.length, 8);
  eq('max 的 label', ext[0].label, 'L:admin.llm.effortMax');
  eq('原 6 档顺序不变', ext.slice(2).map(o => o.value),
     ['high', 'medium', 'low', 'minimal', 'none', 'auto']);
  check('auto 档必须在（后端有该值，缺了无法回显）',
        plain.some(o => o.value === 'auto'));
  check('minimal 档必须在', plain.some(o => o.value === 'minimal'));
  // 不改动入参 / 每次调用返回新数组
  const a1 = M.effortOptions('gpt-5', labelOf);
  const a2 = M.effortOptions('gpt-5', labelOf);
  check('两次调用互不影响（不是共享同一数组）', a1 !== a2 && a1.length === a2.length);
}

// ------------------------------------------------------------ filterProviders
console.log('filterProviders:');
{
  const ps = [
    { id: 'p1', name: 'Alpha', type: 'OpenAI 兼容', group: '自定义' },
    { id: 'p2', name: 'Beta', type: 'Claude', group: '官方' },
    { id: 'gamma', name: 'Gamma' },
  ];
  eq('无 query → 原数组引用（不是拷贝）', M.filterProviders(ps, '') === ps, true);
  eq('按 name 且大小写无关', M.filterProviders(ps, 'alph').map(p => p.id), ['p1']);
  eq('按 type', M.filterProviders(ps, 'claude').map(p => p.id), ['p2']);
  eq('按 group', M.filterProviders(ps, '官方').map(p => p.id), ['p2']);
  eq('按 id', M.filterProviders(ps, 'gamma').map(p => p.id), ['gamma']);
  eq('query 两端空白被 trim', M.filterProviders(ps, '  alph  ').map(p => p.id), ['p1']);
  eq('匹配不到 → 空数组', M.filterProviders(ps, 'zzz'), []);
  eq('providers 为 null → 空数组', M.filterProviders(null, 'x'), []);
  eq('providers 为 undefined → 空数组', M.filterProviders(undefined, 'x'), []);
  eq('空数组 → 空数组', M.filterProviders([], 'x'), []);
}

// ------------------------------------------------------------ filterRemoteModels
console.log('filterRemoteModels:');
{
  const ms = [{ id: 'gpt-x', name: 'GPT X' }, { id: 'other', name: 'Other Model' }];
  eq('无 query → 原数组引用', M.filterRemoteModels(ms, '') === ms, true);
  eq('按 id', M.filterRemoteModels(ms, 'gpt').map(m => m.id), ['gpt-x']);
  eq('按 name', M.filterRemoteModels(ms, 'other model').map(m => m.id), ['other']);
  eq('大小写无关', M.filterRemoteModels(ms, 'GPT').map(m => m.id), ['gpt-x']);
  eq('无 models → 空数组', M.filterRemoteModels(null, 'x'), []);
  // 无 name 的条目不应崩（原实现用 (m.name && ...) 守卫）
  eq('无 name 字段不崩', M.filterRemoteModels([{ id: 'a' }], 'a').map(m => m.id), ['a']);
}

// ------------------------------------------------------------ fallbackOptionsFor
console.log('fallbackOptionsFor:');
{
  const cfg = { active_model_id: 'm1', models: [{ id: 'm1' }, { id: 'm2' }, { id: 'm3' }] };
  eq('排除主模型', M.fallbackOptionsFor(cfg).map(m => m.id), ['m2', 'm3']);
  eq('cfg 为 null → 空数组', M.fallbackOptionsFor(null), []);
  eq('无 models → 空数组', M.fallbackOptionsFor({ active_model_id: 'm1' }), []);
}

// ------------------------------------------------------------ modelDisplayName
console.log('modelDisplayName:');
{
  const ms = [
    { id: 'm1', name: '模型一', provider_name: 'P1' },
    { id: 'm2', name: '模型二' },
    { id: 'm3' },
  ];
  eq('有 provider_name → 名称 · 供应商', M.modelDisplayName(ms, 'm1'), '模型一 · P1');
  eq('无 provider_name → 只名称', M.modelDisplayName(ms, 'm2'), '模型二');
  eq('无 name → 回落 id', M.modelDisplayName(ms, 'm3'), 'm3');
  eq('查不到 → 原样返回 id', M.modelDisplayName(ms, 'nope'), 'nope');
  eq('models 为 null → 原样返回 id', M.modelDisplayName(null, 'x'), 'x');
}

// ------------------------------------------------------------ toggleInArray / moveInArray
console.log('toggleInArray / moveInArray:');
{
  const a = ['x'];
  const r = M.toggleInArray(a, 'y');
  check('原地修改（返回同一引用 —— vue 才能追踪）', r === a, 'not same ref');
  eq('不存在 → 追加', a, ['x', 'y']);
  M.toggleInArray(a, 'x');
  eq('已存在 → 移除', a, ['y']);
  const empty = [];
  M.toggleInArray(empty, 'z');
  eq('空数组 → 加入', empty, ['z']);
}
{
  const a = ['a', 'b', 'c'];
  M.moveInArray(a, 0, 1);
  eq('下移', a, ['b', 'a', 'c']);
  M.moveInArray(a, 2, -1);
  eq('上移', a, ['b', 'c', 'a']);
  const before = [...a];
  M.moveInArray(a, 0, -1);
  eq('越界上移不动', a, before);
  M.moveInArray(a, 2, 1);
  eq('越界下移不动', a, before);
  const single = ['only'];
  M.moveInArray(single, 0, 1);
  eq('单元素不动', single, ['only']);
}

// ------------------------------------------------------------ apiFormatEffect
console.log('apiFormatEffect（切换 api_format 时的路径迁移）:');
{
  const E = M.apiFormatEffect;
  eq('claude_messages ← 标准路径 → /messages',
     E('claude_messages', '/chat/completions'), { apiPath: '/messages', responseApiEnabled: false });
  eq('claude_messages ← 空路径 → /messages',
     E('claude_messages', ''), { apiPath: '/messages', responseApiEnabled: false });
  eq('openai_responses ← 标准路径 → /responses',
     E('openai_responses', '/chat/completions'), { apiPath: '/responses', responseApiEnabled: true });
  eq('openai_chat ← 标准路径 → /chat/completions',
     E('openai_chat', '/messages'), { apiPath: '/chat/completions', responseApiEnabled: false });
  // ⚠️ 自定义路径不得被覆盖
  eq('自定义路径不被覆盖（claude）',
     E('claude_messages', '/custom/path'), { apiPath: '/custom/path', responseApiEnabled: false });
  eq('自定义路径不被覆盖（responses）',
     E('openai_responses', '/my/ep'), { apiPath: '/my/ep', responseApiEnabled: true });
  eq('自定义路径不被覆盖（chat）',
     E('openai_chat', '/x'), { apiPath: '/x', responseApiEnabled: false });
  // response_api_enabled 无条件跟随 format
  check('responses 恒开 response_api', E('openai_responses', '/x').responseApiEnabled === true);
  check('chat 恒关 response_api', E('openai_chat', '/messages').responseApiEnabled === false);
  check('claude 恒关 response_api', E('claude_messages', '/messages').responseApiEnabled === false);
  eq('未知 format + 标准路径 → 不动',
     E('weird', '/chat/completions'), { apiPath: '/chat/completions', responseApiEnabled: false });
  eq('null 路径按空处理', E('claude_messages', null), { apiPath: '/messages', responseApiEnabled: false });
}

// ------------------------------------------------------------ buildRemoteModelPayload
console.log('buildRemoteModelPayload（本刀消掉的逐字重复）:');

/** 批 75：`buildRemoteModelPayload` / `providerDeleteCascadeHint` 改为
 *  **注入 `t`**（与 `effortOptions(labelOf)` 同一约定：本模块不依赖 vue / i18n）。
 *  这里用一个记录调用的假 t —— 既能满足调用契约，又能证明文案确实走了 i18n。 */
const tCalls = [];
const fakeT = (key, _fallback, params) => {
  tCalls.push({ key, params });
  if (key === 'admin.llm.remoteAutoCollected') return '[remoteAutoCollected]';
  if (key === 'admin.llm.cascadeModelsDeleted') return `[cascade:${params?.n}]`;
  return `[${key}]`;
};
{
  const provider = { id: 'p1', name: 'P1', base_url: 'https://a.b', api_format: 'openai_chat' };
  const full = M.buildRemoteModelPayload(
    { id: 'mx', name: 'MX', default_effort: 'low', capabilities: ['vision'],
      context_length: 64000, description: 'D' }, provider, fakeT);
  eq('字段全集', Object.keys(full).sort(),
     ['api_format', 'base_url', 'capabilities', 'context_length', 'description',
      'id', 'name', 'provider_id', 'provider_name', 'reasoning_effort', 'reasoning_type']);
  eq('provider 字段透传', {
    provider_id: full.provider_id, provider_name: full.provider_name, base_url: full.base_url,
  }, { provider_id: 'p1', provider_name: 'P1', base_url: 'https://a.b' });
  eq('显式字段优先', {
    name: full.name, api_format: full.api_format, reasoning_type: full.reasoning_type,
    reasoning_effort: full.reasoning_effort, capabilities: full.capabilities,
  }, { name: 'MX', api_format: 'openai_chat', reasoning_type: 'auto',
       reasoning_effort: 'low', capabilities: ['vision'] });

  // 回落链
  const bare = M.buildRemoteModelPayload({ id: 'mz' }, provider, fakeT);
  eq('无 name → 回落 id', bare.name, 'mz');
  eq('无 api_format → 回落 provider', bare.api_format, 'openai_chat');
  eq('无 reasoning_type → auto', bare.reasoning_type, 'auto');
  eq('无 default_effort → high', bare.reasoning_effort, 'high');
  eq('无 capabilities → [chat]', bare.capabilities, ['chat']);
  eq('无 description → 走注入的 i18n 文案', bare.description, '[remoteAutoCollected]');
  const noFmt = M.buildRemoteModelPayload({ id: 'm' }, { id: 'p' }, fakeT);
  eq('provider 也无 api_format → openai_chat', noFmt.api_format, 'openai_chat');
  // description 截断
  const long = M.buildRemoteModelPayload({ id: 'm', description: 'x'.repeat(300) }, provider, fakeT);
  eq('description 截到 100', long.description.length, 100);
  const empty = M.buildRemoteModelPayload({ id: 'm', description: '' }, provider, fakeT);
  eq('空 description → 回落文案（falsy 判定）', empty.description, '[remoteAutoCollected]');
  eq('context_length 可为 undefined（原实现直接透传）',
     M.buildRemoteModelPayload({ id: 'm' }, provider, fakeT).context_length, undefined);
}

// ------------------------------------------------------------ buildActivatePayload
console.log('buildActivatePayload:');
eq('字段与顺序', M.buildActivatePayload('m1', 'p1', 'high'),
   { model_id: 'm1', provider_id: 'p1', reasoning_effort: 'high' });

// ------------------------------------------------------------ providerDeleteCascadeHint
console.log('providerDeleteCascadeHint:');
eq('models_count 优先', M.providerDeleteCascadeHint({ models_count: 3, models: [1, 2] }, fakeT),
   '[cascade:3]');
eq('无 models_count → 用 models.length',
   M.providerDeleteCascadeHint({ models: [1, 2] }, fakeT), '[cascade:2]');
eq('都没有 → 空串', M.providerDeleteCascadeHint({}, fakeT), '');
eq('0 个 → 空串（不给级联提示）', M.providerDeleteCascadeHint({ models_count: 0 }, fakeT), '');
eq('models_count 为 0 时不再看 models（?? 语义）',
   M.providerDeleteCascadeHint({ models_count: 0, models: [1, 2, 3] }, fakeT), '');
eq('null 不崩', M.providerDeleteCascadeHint(null, fakeT), '');

// 批 75：文案必须经注入的 t —— 硬编码中文在英文界面下会直接显示中文
tCalls.length = 0;
M.buildRemoteModelPayload({ id: 'm' }, { id: 'p' }, fakeT);
eq('buildRemoteModelPayload 的回落文案经 t()',
   tCalls.some((c) => c.key === 'admin.llm.remoteAutoCollected'), true);
tCalls.length = 0;
M.providerDeleteCascadeHint({ models_count: 2 }, fakeT);
eq('providerDeleteCascadeHint 的级联提示经 t() 且带 {n}',
   tCalls, [{ key: 'admin.llm.cascadeModelsDeleted', params: { n: 2 } }]);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
