/**
 * 系统运维与账号管理（AdminSysPage）改密与建号表单前置门禁与实时校验守卫闸（批 128）。
 *
 * ## 实测缺陷与背景：
 *
 * 在系统运维页（`/admin/adminsys`）中：
 * 1. 管理员修改密码面板：后端强制要求新密码至少 12 位；
 * 2. 新建管理员对话框：后端强制要求账号至少 3 位、密码至少 12 位。
 *
 * 缺陷：
 * 此前两处的提交按钮在输入为空或长度不足时保持可点击状态（仅排除了 loading 态）；
 * 用户若在密码未满 12 位或账号未满 3 位时点击提交，系统直接抛出 toast 报错，缺乏前端表单的
 * 前置长度感知与动作门禁；并且输入框在长度不足时不提供任何视觉反馈（无 `.is-bad`）与无障碍错误提示（无 `aria-invalid`）。
 *
 * 修复后：
 * - 改密按钮严格绑定 `:disabled="changingPwd || !pwdReady"`；
 * - 改密新密码框在输入非空且不足 12 位时呈现 `.is-bad` 红色警示与 `:aria-invalid="true"`；
 * - 新建账号按钮严格绑定 `:disabled="creating || !createReady"`；
 * - 账号输入框与密码输入框在非空但长度不足时提供即时 `.is-bad` 警示与 `:aria-invalid="true"` 无障碍反馈。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('AdminSysPage 改密面板必须受 pwdReady 前置门禁控制且具备实时校验状态', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/AdminSysPage.vue'), 'utf8');

  // 改密按钮门禁
  assert.match(
    vue,
    /:disabled="changingPwd\s*\|\|\s*!pwdReady"/,
    'AdminSysPage 改密提交按钮缺少 !pwdReady 前置门禁',
  );

  // 新密码输入框校验状态
  assert.match(
    vue,
    /:class="\{\s*'is-bad':\s*!!newPassword\s*&&\s*newPassword\.length\s*<\s*12\s*\}"/,
    'AdminSysPage newPassword 输入框缺少 is-bad 动态类',
  );
  assert.match(
    vue,
    /:aria-invalid="!!newPassword\s*&&\s*newPassword\.length\s*<\s*12\s*\?\s*'true'\s*:\s*undefined"/,
    'AdminSysPage newPassword 输入框缺少 aria-invalid 动态无障碍状态',
  );
});

test('AdminSysPage 新建账号弹窗必须受 createReady 前置门禁控制且具备实时校验状态', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/AdminSysPage.vue'), 'utf8');

  // 建号按钮门禁
  assert.match(
    vue,
    /:disabled="creating\s*\|\|\s*!createReady"/,
    'AdminSysPage 建号提交按钮缺少 !createReady 前置门禁',
  );

  // 账号输入框校验状态
  assert.match(
    vue,
    /:class="\{\s*'is-bad':\s*!!newUsername\s*&&\s*newUsername\.trim\(\)\.length\s*<\s*3\s*\}"/,
    'AdminSysPage newUsername 输入框缺少 is-bad 动态类',
  );
  assert.match(
    vue,
    /:aria-invalid="!!newUsername\s*&&\s*newUsername\.trim\(\)\.length\s*<\s*3\s*\?\s*'true'\s*:\s*undefined"/,
    'AdminSysPage newUsername 输入框缺少 aria-invalid 动态无障碍状态',
  );

  // 密码输入框校验状态
  assert.match(
    vue,
    /:class="\{\s*'is-bad':\s*!!newPasswordForCreate\s*&&\s*newPasswordForCreate\.length\s*<\s*12\s*\}"/,
    'AdminSysPage newPasswordForCreate 输入框缺少 is-bad 动态类',
  );
  assert.match(
    vue,
    /:aria-invalid="!!newPasswordForCreate\s*&&\s*newPasswordForCreate\.length\s*<\s*12\s*\?\s*'true'\s*:\s*undefined"/,
    'AdminSysPage newPasswordForCreate 输入框缺少 aria-invalid 动态无障碍状态',
  );
});
