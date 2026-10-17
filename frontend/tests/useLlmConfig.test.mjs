/**
 * `src/composables/useLlmConfig.ts` 行为基线
 * （结构优化阶段 4·B3 第五十六刀）。
 *
 * ## 为什么要先立这个文件
 *
 * `useLlmConfig.ts` 是前端最大的单文件（686 行，~40 个函数，一个闭包里的
 * ~50 个绑定）。它的 docstring 自己写了后续拆法（provider / model / failover 三域），
 * 但**它此前没有任何测试** —— 无验证网就拆 686 行闭包，风险与收益不成比例。
 *
 * 故本文件先建立**桩驱动的行为基线**，再去拆。断言的都是"从外部可观察的行为"：
 * 发往服务端的请求体、状态迁移、computed 的产出 —— 而不是内部实现细节，
 * 这样按域拆分后这些断言**仍然有效**（拆分不应改变任何一条）。
 *
 * ## 手法（沿用第五十四刀 `http.test.mjs`）
 *
 * `module.registerHooks` 把四个 composable 依赖换成受控替身：
 *
 * | 依赖 | 替身行为 |
 * |---|---|
 * | `./useApi` | 记录每次 `api(path, opts)`，按 URL 返回预置响应或抛错 |
 * | `./useToast` | 记录 `ok/err/warn/info` 调用 |
 * | `./useConfirm` | `ask()` 立即返回预置答案 |
 * | `./useI18n` | `t(key)` 返回 key 本身（断言就不用绑死中文文案） |
 *
 * ✅ 无需安装任何依赖（符合"禁止增减依赖"）：Node 24 直接跑 `.ts`。
 * 用真实 `vue` 的 `ref`/`computed`（`reactivity` 在组件外可用）。
 *
 * ## ⚠️ 桩匹配规则（我在这条上连续误判了两次）
 *
 * `setResponse(prefix, fn)` 用 **`startsWith` 前缀**匹配、先注册者优先。
 * 所以注册的串**必须是真实 API 路径的前缀**。
 *
 * 我第一版给 `toggleProviderQuick` 注册的是结尾片段 `'/toggle'` ——
 * 真实路径 `/api/v1/admin/llm/providers/p2/toggle` **不以** `'/toggle'` 开头，
 * 于是掉进默认 `{}`，症状是 `p.enabled` 变成 `undefined`。
 *
 * 更该记的是我**随后的误判**：我先把它归因为"宽前缀 `models` 遮住了窄前缀"，
 * 并据此改了注册顺序 —— 改完照样失败（顺序与匹配无关，`startsWith` 本来就不成立）。
 * 直到把渲染出的匹配过程打出来，才看出是注册串本身写错。
 *
 * **教训**：桩不生效时，先打印"注册了哪些前缀 + 实际请求路径"，再猜原因。
 *
 * ⚠️ `onMounted` 在组件外调用**不会执行**（Vue 会告警），故测试里显式调 `loadConfig()`。
 *
 * 运行：
 *     node --experimental-strip-types tests/useLlmConfig.test.mjs
 */
import { registerHooks } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const SRC = path.resolve('src');

// ---------------------------------------------------------------- 替身注册
/** 每个测试用独立 harness，互不串状态。 */
function makeStubs() {
  const calls = [];          // { path, opts, body }
  const toasts = [];         // { kind, title, desc }
  const responses = new Map(); // path(前缀) → () => Promise<any>
  let confirmAnswer = true;
  let fallbackHandler = null;

  const api = async (p, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path: p, opts, body });
    if (fallbackHandler) {
      const r = await fallbackHandler(p, opts, body);
      if (r !== undefined) return r;
    }
    for (const [prefix, fn] of responses) {
      if (p.startsWith(prefix)) return fn(p, body);
    }
    return {};
  };

  return {
    calls, toasts, responses,
    setResponse: (prefix, fn) => responses.set(prefix, fn),
    setConfirm: (v) => { confirmAnswer = v; },
    onCall: (fn) => { fallbackHandler = fn; },
    get confirmAnswer() { return confirmAnswer; },
    api,
  };
}

let STUBS = makeStubs();

/**
 * ⚠️ 桩响应表是**模块级全局**（跨 block 复用）。`harness()` 每次都换一份新的，
 * 但**注册过的串在同一个 harness 内累加**且用 `startsWith` 匹配 ——
 * 注册串必须是真实路径的前缀（见文件头"桩匹配规则"）。
 */
const STUB_FILES = {
  'useApi.ts': `
    export function useApi() { return { loading: { value: false }, error: { value: null }, api: globalThis.__LLM_STUBS__.api } }
  `,
  'useToast.ts': `
    export function useToast() {
      const rec = (k) => (title, desc) => globalThis.__LLM_STUBS__.toasts.push({ kind: k, title, desc })
      return { items: [], ok: rec('ok'), err: rec('err'), warn: rec('warn'), info: rec('info'), dismiss: () => {} }
    }
  `,
  'useConfirm.ts': `
    export function useConfirm() {
      return { state: { value: { open: false } }, ask: async () => globalThis.__LLM_STUBS__.confirmAnswer, settle: () => {} }
    }
  `,
  'useI18n.ts': `
    export function useI18n() {
      return { t: (k) => k, currentLocale: { value: 'zh-CN' }, applyLocale: () => {}, toggleLocale: () => {} }
    }
  `,
};

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL) {
      const base = path.dirname(fileURLToPath(context.parentURL));
      const cand = path.join(base, specifier);
      for (const c of [cand + '.ts', cand + '.vue', cand]) {
        if (fs.existsSync(c) && fs.statSync(c).isFile()) {
          return { url: pathToFileURL(c).href, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    const file = fileURLToPath(url);
    if (!file.startsWith(SRC)) return nextLoad(url, context);
    const name = path.basename(file);
    if (STUB_FILES[name]) {
      return { format: 'module', shortCircuit: true, source: STUB_FILES[name] };
    }
    return nextLoad(url, context);
  },
});

const MOD = pathToFileURL(path.join(SRC, 'composables', 'useLlmConfig.ts')).href;

/** 取一份全新的 composable 实例。⚠️ 每次清空桩响应表，避免跨 block 的宽前缀遮蔽。 */
async function harness() {
  STUBS = makeStubs();
  globalThis.__LLM_STUBS__ = STUBS;
  // 加 cache-buster 让每次 import 都是新模块实例（模块级 ref 会串状态）
  const mod = await import(MOD + '?v=' + Math.random());
  return { c: mod.useLlmConfig(), s: STUBS };
}

// ---------------------------------------------------------------- 断言工具
let pass = 0, fail = 0;
function check(name, cond, extra = '') {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  → ' + extra : '')); }
}
const eq = (name, got, want) =>
  check(name, JSON.stringify(got) === JSON.stringify(want),
        'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));

/** 造一份最小可用的 `/models` 响应。 */
function cfgPayload(over = {}) {
  return {
    active_model_id: 'm1',
    active_reasoning_effort: 'high',
    thinking_timeout: 180,
    request_attempts: 4,
    fallback_model_ids: ['m2'],
    max_fallback_models: 3,
    models: [
      { id: 'm1', name: '模型一', provider_name: 'P1' },
      { id: 'm2', name: '模型二', provider_name: 'P1' },
      { id: 'm3', name: '模型三', provider_name: 'P2' },
      { id: 'm4', name: '模型四' },
    ],
    providers: [
      { id: 'p1', name: '供应商一', enabled: true },
      { id: 'p2', name: '供应商二', enabled: false },
    ],
    ...over,
  };
}

// ================================================================ loadConfig
console.log('loadConfig（onMounted 的主体，组件外需显式调用）:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  eq('loading 复位', c.loading.value, false);
  eq('cfg 被写入', c.cfg.value.active_model_id, 'm1');
  eq('thinkingTimeoutInput ← thinking_timeout', c.thinkingTimeoutInput.value, 180);
  eq('requestAttemptsInput ← request_attempts', c.requestAttemptsInput.value, 4);
  eq('fallbackIds ← fallback_model_ids（拷贝，不是同一引用）', c.fallbackIds.value, ['m2']);
  eq('请求了 /models', s.calls.map(x => x.path), ['/api/v1/admin/llm/models']);
}
{
  // 缺字段时不得把状态改坏
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => ({ models: [], providers: [] }));
  await c.loadConfig();
  eq('无 thinking_timeout → 保留默认 120', c.thinkingTimeoutInput.value, 120);
  eq('无 request_attempts → 保留默认 3', c.requestAttemptsInput.value, 3);
  eq('无 fallback_model_ids → 保持空数组', c.fallbackIds.value, []);
  eq('loading 仍复位', c.loading.value, false);
}
{
  // 失败路径：吞掉异常并复位 loading（不抛给调用方）
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => { throw new Error('boom'); });
  let threw = false;
  try { await c.loadConfig(); } catch { threw = true; }
  check('loadConfig 失败不抛（内部 catch）', !threw);
  eq('失败后 loading 仍复位', c.loading.value, false);
  eq('失败后 cfg 保持 null', c.cfg.value, null);
}

// ============================================================== fallback 域
console.log('fallback 域（toggleFallback / moveFallback / fallbackOptions）:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  // 初始 ['m2'] → toggle m3 加入
  c.toggleFallback('m3');
  eq('toggle 未选中的 → 追加', c.fallbackIds.value, ['m2', 'm3']);
  c.toggleFallback('m2');
  eq('toggle 已选中的 → 移除', c.fallbackIds.value, ['m3']);
  c.toggleFallback('m3');
  eq('再 toggle → 回到空', c.fallbackIds.value, []);
}
{
  // 上限：max_fallback_models = 3
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload({ fallback_model_ids: [] }));
  await c.loadConfig();
  c.toggleFallback('m2'); c.toggleFallback('m3'); c.toggleFallback('m4');
  eq('加到上限 3', c.fallbackIds.value.length, 3);
  c.toggleFallback('m1');   // 第 4 个 → 拒绝
  eq('超上限被拒（仍是 3 个）', c.fallbackIds.value.length, 3);
  check('超上限给出错误提示', c.settingsResult.value && c.settingsResult.value.ok === false,
        JSON.stringify(c.settingsResult.value));
}
{
  // 上限缺省值 5（⚠️ 必须用**互不相同**的 id：重复 toggle 同一个是"移除"）
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models',
    async () => cfgPayload({ max_fallback_models: undefined, fallback_model_ids: [] }));
  await c.loadConfig();
  for (const id of ['m1', 'm2', 'm3', 'm4']) c.toggleFallback(id);
  eq('无 max_fallback_models → 缺省上限允许到 5，故 4 个都能加', c.fallbackIds.value.length, 4);
  // 再加第 5 个（不同 id）也应允许
  const { c: c2, s: s2 } = await harness();
  s2.setResponse('/api/v1/admin/llm/models', async () => cfgPayload({
    max_fallback_models: undefined,
    fallback_model_ids: [],
    models: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }, { id: 'e' }, { id: 'f' }],
  }));
  await c2.loadConfig();
  for (const id of ['a', 'b', 'c', 'd', 'e']) c2.toggleFallback(id);
  eq('第 5 个仍可加（上限含 5）', c2.fallbackIds.value.length, 5);
  c2.toggleFallback('f');
  eq('第 6 个被拒', c2.fallbackIds.value.length, 5);
}
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload({ fallback_model_ids: ['m2', 'm3', 'm4'] }));
  await c.loadConfig();
  c.moveFallback(0, 1);
  eq('moveFallback 下移', c.fallbackIds.value, ['m3', 'm2', 'm4']);
  c.moveFallback(2, -1);
  eq('moveFallback 上移', c.fallbackIds.value, ['m3', 'm4', 'm2']);
  const before = [...c.fallbackIds.value];
  c.moveFallback(0, -1);
  eq('越界上移不动', c.fallbackIds.value, before);
  c.moveFallback(2, 1);
  eq('越界下移不动', c.fallbackIds.value, before);
}
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  // fallbackOptions 排除当前主模型
  eq('fallbackOptions 排除 active_model_id',
     c.fallbackOptions.value.map(m => m.id), ['m2', 'm3', 'm4']);
  eq('modelNameOf 带 provider_name', c.modelNameOf('m1'), '模型一 · P1');
  eq('modelNameOf 无 provider_name', c.modelNameOf('m4'), '模型四');
  eq('modelNameOf 未知 id 原样返回', c.modelNameOf('nope'), 'nope');
}

// ============================================================ filteredProviders
console.log('filteredProviders（搜索）:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload({
    providers: [
      { id: 'p1', name: 'Alpha', enabled: true },
      { id: 'p2', name: 'Beta', enabled: false, models_count: 2 },
    ],
  }));
  await c.loadConfig();
  eq('无搜索词 → 全部', c.filteredProviders.value.length, 2);
  c.searchQuery.value = 'alph';
  eq('按 name 大小写无关匹配', c.filteredProviders.value.map(p => p.id), ['p1']);
  c.searchQuery.value = 'ZZZ';
  eq('匹配不到 → 空', c.filteredProviders.value, []);
  c.searchQuery.value = '';
  eq('清空搜索 → 恢复全部', c.filteredProviders.value.length, 2);
}

// ============================================================ availableEffort
console.log('availableEffortOptions（按模型族给推理档位）:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  const valuesFor = (id) => {
    c.modelForm.value = { ...c.modelForm.value, id };
    return c.availableEffortOptions.value.map(o => o.value);
  };
  const gpt = valuesFor('gpt-5-codex');
  check('gpt-5-codex 有 minimal 档', gpt.includes('minimal'), JSON.stringify(gpt));
  const o3 = valuesFor('o3-mini');
  check('o3 系列有档位', o3.length > 0, JSON.stringify(o3));
  const other = valuesFor('claude-sonnet-4');
  check('其它模型族也有档位', other.length > 0, JSON.stringify(other));
  const empty = valuesFor('');
  check('空 id 不崩', Array.isArray(empty));
}

// ========================================================= 全局设置保存
console.log('saveGlobalSettings（请求体是主要契约）:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.thinkingTimeoutInput.value = 240;
  c.requestAttemptsInput.value = 5;
  c.fallbackIds.value = ['m2', 'm3'];
  s.calls.length = 0;
  await c.saveGlobalSettings();
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/settings');
  check('POST 到 /settings', !!post, JSON.stringify(s.calls.map(x => x.path)));
  eq('请求体逐字', post.body, {
    thinking_timeout: 240,
    active_model_id: 'm1',
    reasoning_effort: 'high',
    request_attempts: 5,
    fallback_model_ids: ['m2', 'm3'],
  });
  eq('method 是 POST', post.opts.method, 'POST');
  check('保存后重新拉配置', s.calls.some(x => x.path === '/api/v1/admin/llm/models'));
  check('保存后拉 failover 事件',
        s.calls.some(x => x.path === '/api/v1/admin/llm/failover-events'));
  check('成功提示 ok:true', c.settingsResult.value && c.settingsResult.value.ok === true);
  eq('savingSettings 复位', c.savingSettings.value, false);
}
{
  // 非数字输入回落默认值
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.thinkingTimeoutInput.value = NaN;
  c.requestAttemptsInput.value = 0;
  s.calls.length = 0;
  await c.saveGlobalSettings();
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/settings');
  eq('NaN → 120', post.body.thinking_timeout, 120);
  eq('0 → 3（falsy 也回落）', post.body.request_attempts, 3);
}
{
  // 失败路径
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  s.setResponse('/api/v1/admin/llm/settings', async () => { throw new Error('保存失败'); });
  await c.saveGlobalSettings();
  check('失败给出 ok:false', c.settingsResult.value && c.settingsResult.value.ok === false);
  eq('失败文案取 err.message', c.settingsResult.value.error, '保存失败');
  eq('savingSettings 仍复位', c.savingSettings.value, false);
}

// ============================================================ provider 域
console.log('provider 域:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.openAddProviderModal();
  eq('切到 detail 视图', c.currentView.value, 'detail');
  eq('is_new 标记', c.selectedProvider.value.is_new, true);
  eq('表单默认 api_format', c.providerForm.value.api_format, 'openai_chat');
  eq('表单默认 api_path', c.providerForm.value.api_path, '/chat/completions');
  eq('表单 id 为空（保存时由 name 派生）', c.providerForm.value.id, '');
  eq('enabled 默认开', c.providerForm.value.enabled, true);
}
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  // selectProvider：claude 无 api_format 时按 id 推断
  c.selectProvider({ id: 'claude', name: 'Claude' });
  eq('claude → claude_messages', c.providerForm.value.api_format, 'claude_messages');
  eq('claude → /messages', c.providerForm.value.api_path, '/messages');
  c.selectProvider({ id: 'x', name: 'X' });
  eq('其它 → openai_chat', c.providerForm.value.api_format, 'openai_chat');
  c.selectProvider({ id: 'y', name: 'Y', api_format: 'openai_responses' });
  eq('显式 api_format 优先', c.providerForm.value.api_format, 'openai_responses');
  eq('openai_responses → /responses', c.providerForm.value.api_path, '/responses');
  eq('选中后 api_key 清空', c.providerForm.value.api_key, '');
  c.goBackToList();
  eq('goBackToList 回到 list', c.currentView.value, 'list');
  eq('goBackToList 清空选中', c.selectedProvider.value, null);
}
{
  // onApiFormatChange 的路径迁移
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.providerForm.value = { ...c.providerForm.value, api_path: '/chat/completions', api_format: 'openai_chat' };
  c.providerForm.value.api_format = 'claude_messages';
  c.onApiFormatChange();
  eq('→ claude_messages 改路径', c.providerForm.value.api_path, '/messages');
  eq('→ claude_messages 关 response_api', c.providerForm.value.response_api_enabled, false);
  c.providerForm.value.api_format = 'openai_responses';
  c.onApiFormatChange();
  eq('→ openai_responses 改路径', c.providerForm.value.api_path, '/responses');
  eq('→ openai_responses 开 response_api', c.providerForm.value.response_api_enabled, true);
  c.providerForm.value.api_format = 'openai_chat';
  c.onApiFormatChange();
  eq('→ openai_chat 改回路径', c.providerForm.value.api_path, '/chat/completions');
  // 自定义路径不得被覆盖
  c.providerForm.value.api_path = '/custom/path';
  c.providerForm.value.api_format = 'claude_messages';
  c.onApiFormatChange();
  eq('自定义路径不被覆盖', c.providerForm.value.api_path, '/custom/path');
}
{
  // saveProviderConfig：id 派生 + 空 api_key 删除
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.openAddProviderModal();
  c.providerForm.value = { ...c.providerForm.value, name: 'My Provider!', api_key: '' };
  s.calls.length = 0;
  await c.saveProviderConfig();
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/providers');
  check('POST /providers', !!post, JSON.stringify(s.calls.map(x => x.path)));
  eq('id 由 name 派生并净化', post.body.id, 'my_provider_');
  check('空 api_key 被删除', !('api_key' in post.body), JSON.stringify(post.body));
}
{
  // toggleProviderQuick
  const { c, s } = await harness();
  // ⚠️ `setResponse` 是 **`startsWith` 前缀**匹配，注册的串必须是真实 API 路径的
  //    **前缀**。我第一版写的是结尾片段 `'/toggle'` —— 而真实路径是
  //    `/api/v1/admin/llm/providers/p2/toggle`，它**不以** `'/toggle'` 开头，
  //    于是掉进默认 `{}`，症状是 `p.enabled` 变成 undefined。
  //    （不是"宽前缀遮蔽"，就是注册串写错了 —— 我先前那条注释误导了自己。）
  s.setResponse('/api/v1/admin/llm/providers/', async () => ({ enabled: true }));
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  const p = { id: 'p2', enabled: false };
  let stopped = false;
  await c.toggleProviderQuick(p, { stopPropagation: () => { stopped = true; } });
  check('stopPropagation 被调用', stopped);
  const call = s.calls.find(x => x.path.includes('/toggle'));
  check('请求 /toggle', !!call, JSON.stringify(s.calls.map(x => x.path)));
  eq('请求体 enabled 取反', call.body, { enabled: true });
  eq('响应回写 p.enabled', p.enabled, true);
}
{
  // removeProvider：确认框为假 → 不发请求
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1' });
  s.setConfirm(false);
  s.calls.length = 0;
  await c.removeProvider();
  eq('确认否决 → 无删除请求', s.calls.filter(x => x.opts.method === 'DELETE').length, 0);
  eq('确认否决 → 仍在 detail 视图', c.currentView.value, 'detail');
}
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1' });
  s.setConfirm(true);
  s.calls.length = 0;
  await c.removeProvider();
  const del = s.calls.find(x => x.opts.method === 'DELETE');
  check('确认通过 → 发 DELETE', !!del, JSON.stringify(s.calls.map(x => [x.path, x.opts.method])));
  check('DELETE 命中该 provider', del.path.includes('/providers/p1'), del.path);
  eq('删除后回到 list', c.currentView.value, 'list');
}

// ============================================================ 远端拉取域
console.log('远端拉取域:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1', base_url: 'https://api.example.com' });
  s.setResponse('/api/v1/admin/llm/fetch-models', async () => ({
    ok: true,
    models: [{ id: 'gpt-x', name: 'GPT X' }, { id: 'other', name: 'Other' }],
  }));
  c.customFetchUrl.value = '';
  c.customFetchKey.value = 'secret-key';
  await c.executeRemoteFetch();
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/fetch-models');
  eq('无自定义 URL → 回落 provider.base_url', post.body.base_url, 'https://api.example.com');
  eq('provider_id 传入', post.body.provider_id, 'p1');
  eq('自定义 key 传入', post.body.api_key, 'secret-key');
  eq('结果写入 remoteFetchResult', c.remoteFetchResult.value.ok, true);
  eq('fetchingRemote 复位', c.fetchingRemote.value, false);
  eq('filteredRemoteModels 无搜索词 → 全部', c.filteredRemoteModels.value.length, 2);
  c.remoteSearch.value = 'gpt';
  eq('按 id 过滤', c.filteredRemoteModels.value.map(m => m.id), ['gpt-x']);
  c.remoteSearch.value = 'other';
  eq('按 name/id 过滤', c.filteredRemoteModels.value.map(m => m.id), ['other']);
}
{
  // 无自定义 key 时不传 api_key
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1', base_url: 'https://a.b' });
  s.setResponse('/api/v1/admin/llm/fetch-models', async () => ({ ok: true, models: [] }));
  c.customFetchKey.value = '';
  s.calls.length = 0;
  await c.executeRemoteFetch();
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/fetch-models');
  check('无自定义 key → 不带 api_key 字段', !('api_key' in post.body), JSON.stringify(post.body));
}
{
  // 失败路径
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1', base_url: 'https://a.b' });
  s.setResponse('/api/v1/admin/llm/fetch-models', async () => { throw new Error('拉取失败'); });
  await c.executeRemoteFetch();
  eq('失败写入 ok:false', c.remoteFetchResult.value.ok, false);
  eq('失败文案', c.remoteFetchResult.value.error, '拉取失败');
  eq('fetchingRemote 仍复位', c.fetchingRemote.value, false);
}
{
  // importRemoteModel 的请求体
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1', base_url: 'https://a.b', api_format: 'openai_chat' });
  s.calls.length = 0;
  await c.importRemoteModel({ id: 'mx', name: 'MX', context_length: 128000 });
  const post = s.calls.find(x => x.path === '/api/v1/admin/llm/models' && x.opts.method === 'POST');
  check('收录模型 POST /models', !!post, JSON.stringify(s.calls.map(x => x.path)));
  eq('payload 关键字段', {
    id: post.body.id, name: post.body.name, provider_id: post.body.provider_id,
    provider_name: post.body.provider_name, base_url: post.body.base_url,
    api_format: post.body.api_format, reasoning_effort: post.body.reasoning_effort,
    context_length: post.body.context_length,
  }, {
    id: 'mx', name: 'MX', provider_id: 'p1', provider_name: 'P1',
    base_url: 'https://a.b', api_format: 'openai_chat', reasoning_effort: 'high',
    context_length: 128000,
  });
  eq('无 autoActivate → 不激活',
     s.calls.filter(x => x.path.includes('/activate')).length, 0);
  check('成功 toast', s.toasts.some(t => t.kind === 'ok'), JSON.stringify(s.toasts));
}
{
  // autoActivate=true
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.selectProvider({ id: 'p1', name: 'P1', base_url: 'https://a.b' });
  s.calls.length = 0;
  await c.importRemoteModel({ id: 'mx', name: 'MX', default_effort: 'low' }, true);
  const act = s.calls.find(x => x.path.includes('/activate'));
  check('autoActivate → 发 /activate', !!act, JSON.stringify(s.calls.map(x => x.path)));
  eq('activate 体', act.body, { model_id: 'mx', provider_id: 'p1', reasoning_effort: 'low' });
}
{
  // 未选中 provider → 直接返回，不发请求
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  s.calls.length = 0;
  await c.importRemoteModel({ id: 'mx' });
  eq('无选中 provider → 无请求', s.calls.length, 0);
}

// ============================================================ 杂项
console.log('杂项:');
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  c.setPresetTimeout(300);
  eq('setPresetTimeout 写输入', c.thinkingTimeoutInput.value, 300);
  c.toggleCapability('vision');
  check('toggleCapability 加入', c.modelForm.value.capabilities.includes('vision'),
        JSON.stringify(c.modelForm.value.capabilities));
  c.toggleCapability('vision');
  check('toggleCapability 移除', !c.modelForm.value.capabilities.includes('vision'),
        JSON.stringify(c.modelForm.value.capabilities));
}
{
  const { c, s } = await harness();
  s.setResponse('/api/v1/admin/llm/models', async () => cfgPayload());
  await c.loadConfig();
  s.setResponse('/api/v1/admin/llm/failover-events', async () => ({ events: [{ id: 1 }] }));
  await c.loadFailoverEvents();
  eq('failover 事件写入', c.failoverEvents.value.length, 1);
  s.setResponse('/api/v1/admin/llm/failover-events', async () => { throw new Error('x'); });
  await c.loadFailoverEvents();
  eq('失败 → 清空为 []（不抛）', c.failoverEvents.value, []);
}
{
  // 导出面：模板靠解构取用，键名不得少
  const { c } = await harness();
  const keys = Object.keys(c);
  check('导出键数 ≥ 45', keys.length >= 45, String(keys.length));
  for (const k of ['cfg', 'loading', 'searchQuery', 'currentView', 'selectedProvider',
                   'detailTab', 'providerForm', 'testResult', 'testLoading',
                   'fetchModalVisible', 'remoteFetchResult', 'modelModalVisible',
                   'modelForm', 'fallbackIds', 'fallbackOptions', 'failoverEvents',
                   'saveGlobalSettings', 'loadConfig', 'toggleFallback', 'moveFallback',
                   'removeProvider', 'saveProviderConfig', 'importRemoteModel',
                   'executeRemoteFetch', 'runTestModel', 'activateModel',
                   'deleteSingleModel', 'saveModelForm', 'goBackToList']) {
    check('导出 ' + k, k in c);
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
