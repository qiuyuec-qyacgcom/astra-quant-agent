/**
 * LLM 配置与敏感信息显隐交互细节与单复数守卫闸（批 117）。
 *
 * ## 实测出来的 2 处细节缺陷：
 *
 * 1. 英文环境下单复数语法错位（"1 attempts"）：
 *    在 `/admin/llm` 页面重试次数预设按钮上，英文文案写死为 `{n} attempts`，
 *    导致首个按钮渲染为 "1 attempts"（单数动词复数形式）。
 *    输入框右侧单位在值为 1 时也显示为 "1 attempts"。
 *    修法：拆分 `times1: '{n} attempt'` 与 `timesN: '{n} attempts'`，
 *    单位拆分 `timesUnitSingular: 'attempt'` 与 `timesUnit: 'attempts'`，
 *    按值自适应切换。
 *
 * 2. API Key / 密码显隐按钮无 accessible name 或文案荒谬：
 *    - `ProviderDetailView.vue` 中的 Key 眼睛切换按钮写成了：
 *      `:title="showApiKey ? t('admin.llm.cancel') : 'API Key'"`
 *      展开时提示"取消"（Cancel），收起时提示"API Key"，且没有 `:aria-label`！
 *    - `LoginPage.vue` 中的密码眼睛按钮只有 `:title`，缺少 `:aria-label`。
 *    修法：补齐 `showApiKey` / `hideApiKey` 国际化文案，
 *    眼睛按钮统一绑定动态 `:title` 与 `:aria-label`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { zhAdminLlm } from '../src/locales/zh/admin/llm.ts';
import { enAdminLlm } from '../src/locales/en/admin/llm.ts';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('zh 与 en 语言包必须完整具备单模型请求次数的单复数文案', () => {
  // 中文
  assert.equal(zhAdminLlm.times1, '{n} 次');
  assert.equal(zhAdminLlm.timesN, '{n} 次');
  assert.equal(zhAdminLlm.timesUnitSingular, '次');
  assert.equal(zhAdminLlm.timesUnit, '次');

  // 英文：单数 attempt，复数 attempts，严禁 "1 attempts"
  assert.equal(enAdminLlm.times1, '{n} attempt');
  assert.equal(enAdminLlm.timesN, '{n} attempts');
  assert.equal(enAdminLlm.timesUnitSingular, 'attempt');
  assert.equal(enAdminLlm.timesUnit, 'attempts');
});

test('zh 与 en 语言包必须完整具备 API Key 显隐的准确文案', () => {
  assert.equal(zhAdminLlm.showApiKey, '显示 API Key');
  assert.equal(zhAdminLlm.hideApiKey, '隐藏 API Key');

  assert.equal(enAdminLlm.showApiKey, 'Show API key');
  assert.equal(enAdminLlm.hideApiKey, 'Hide API key');
});

test('ProviderListView 预设按钮与单位必须支持单数 attempt 区分', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/llm/ProviderListView.vue'), 'utf8');
  assert.match(
    vue,
    /n === 1 \? t\('admin\.llm\.times1'[^)]*\) : t\('admin\.llm\.timesN'/,
    'ProviderListView 预设按钮缺少单数 times1 分支判断',
  );
  assert.match(
    vue,
    /requestAttemptsInput === 1 \? t\('admin\.llm\.timesUnitSingular'\) : t\('admin\.llm\.timesUnit'\)/,
    'ProviderListView 单位缺少单数 timesUnitSingular 分支判断',
  );
});

test('ProviderDetailView 眼睛按钮严禁出现 cancel 误报且必须具备 aria-label', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/llm/ProviderDetailView.vue'), 'utf8');
  assert.doesNotMatch(
    vue,
    /showApiKey \? t\('admin\.llm\.cancel'\)/,
    'ProviderDetailView 眼睛按钮的 title 绝不能错误使用 cancel 文案',
  );
  assert.match(
    vue,
    /:title="showApiKey \? t\('admin\.llm\.hideApiKey'\) : t\('admin\.llm\.showApiKey'\)"/,
    'ProviderDetailView 眼睛按钮缺少正确的动态 title',
  );
  assert.match(
    vue,
    /:aria-label="showApiKey \? t\('admin\.llm\.hideApiKey'\) : t\('admin\.llm\.showApiKey'\)"/,
    'ProviderDetailView 眼睛按钮缺少对应的动态 aria-label',
  );
});

test('LoginPage 密码显隐按钮必须具备与 title 对齐的 aria-label', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/LoginPage.vue'), 'utf8');
  assert.match(
    vue,
    /:aria-label="showPwd \? t\('admin\.login\.hidePwd'\) : t\('admin\.login\.showPwd'\)"/,
    'LoginPage 密码眼睛按钮缺少对应的动态 aria-label',
  );
});
