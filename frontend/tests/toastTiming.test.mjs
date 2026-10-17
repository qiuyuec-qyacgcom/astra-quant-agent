/**
 * 轻提示（toast）的**计时语义**守卫闸（批 108）。
 *
 * 为什么值得单独钉：`useToast` 的注释一直写着 `duration: 0 = 不自动消失`，
 * 但实现是 `const ttl = duration || 默认值` —— `0` 是假值，**传 0 反而拿到默认时长**；
 * 而且公开 API 只收 `(title, desc)`，全仓 **170 个调用点没有一个能设时长**。
 * 也就是说这条注释描述的能力**根本到不了**。同时「悬停/聚焦暂停」也不存在，
 * 3.2s 到点即走，带第二行建议文案的错误经常读不完。
 *
 * 这里用**真实计时器 + 很短的时长**测行为，不依赖假定时器：
 * 每条断言都只等几十毫秒，整套仍在百毫秒级。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { useToast } from '../src/composables/useToast.ts';

const SRC = path.resolve(import.meta.dirname, '..', 'src');
const toast = useToast();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const titles = () => toast.items.value.map((x) => x.title);
const clearAll = () => { for (const it of [...toast.items.value]) toast.dismiss(it.id); };

test('默认时长：普通 3200ms、错误 6000ms', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.ok('a');
  toast.err('b');
  assert.deepEqual(toast.items.value.map((x) => x.duration), [3200, 6000]);
});

test('duration = 0 必须真的「不自动消失」（原来传 0 会拿到默认时长）', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.info('常驻', undefined, 0);
  assert.equal(toast.items.value[0].duration, 0, '传 0 没被当成常驻');
  await sleep(160);
  assert.deepEqual(titles(), ['常驻'], 'duration=0 的提示被自动关掉了');
  clearAll();
});

test('自定义时长会按时自动消失，且手动关闭后不会再被「补删」一次', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.ok('短', undefined, 40);
  await sleep(120);
  assert.deepEqual(titles(), [], '自定义短时长没有自动消失');

  toast.ok('手动', undefined, 40);
  assert.deepEqual(titles(), ['手动']);
  toast.dismiss(toast.items.value[0].id);
  assert.deepEqual(titles(), [], '手动关闭失败');
  await sleep(120);                       // 原来的定时器若没清掉，这里会再删一次（无副作用，但会掩盖泄漏）
  assert.deepEqual(titles(), [], '手动关闭后状态被改回去了');
});

test('暂停期间不消失，恢复后按剩余时间继续', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.warn('读久一点', undefined, 120);
  await sleep(50);
  toast.pause();
  await sleep(220);                        // 远超原时长
  assert.deepEqual(titles(), ['读久一点'], '暂停没生效，提示还是走了');
  toast.resume();
  await sleep(160);                        // 剩余约 70ms，足够走完
  assert.deepEqual(titles(), [], '恢复后没有继续倒计时（变成了永久停留）');
});

test('暂停 = 停表：暂停多久都还在，恢复后只需走完**剩余**时间', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.info('停表', undefined, 30);
  await sleep(10);
  toast.pause();
  await sleep(200);                        // 远超原时长
  assert.deepEqual(titles(), ['停表'], '暂停期间越过了原到期点就被删了（应当是「停表」）');
  toast.resume();
  assert.deepEqual(titles(), ['停表'], '恢复的瞬间就删了 —— 剩余时间应当还有约 20ms');
  await sleep(160);
  assert.deepEqual(titles(), [], '恢复后没有继续倒计时（变成了永久停留）');
});

test('到期瞬间才暂停的竞态：恢复时立即收掉，不留残余', async (t) => {
  // 真的构造这个竞态：`pause()` 只对**已挂计时器**的条目记账，
  // 若暂停发生在「到期点已过、回调还没跑」的窗口内，剩余时间会算成 0。
  // 用**忙等**卡住事件循环，计时器回调就没机会执行 —— 窗口是确定性的。
  t.after(clearAll);
  clearAll();
  toast.ok('竞态', undefined, 20);
  const until = Date.now() + 60;
  while (Date.now() < until) { /* 忙等：不 yield，回调无法执行 */ }
  toast.pause();                           // 此刻 deadline 已过，剩余算成 0
  assert.deepEqual(titles(), ['竞态'], '前提不成立：计时器回调在忙等期间跑掉了');
  toast.resume();
  assert.deepEqual(titles(), [], '暂停期间已到期的条目在恢复后没有立刻收掉');
});

test('重复 pause 是幂等的，不得把剩余时间清零', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.ok('幂等', undefined, 90);
  toast.pause();
  await sleep(50);
  toast.pause();                           // 第二次暂停不应改写剩余时间
  toast.resume();
  assert.deepEqual(titles(), ['幂等'], '重复 pause 把剩余时间清零了（会变成永久停留）');
  await sleep(160);
  assert.deepEqual(titles(), [], '恢复后没有收掉');
});

test('常驻提示（duration=0）不受暂停/恢复影响', async (t) => {
  t.after(clearAll);
  clearAll();
  toast.err('常驻错误', '第二行', 0);
  toast.pause();
  await sleep(60);
  toast.resume();
  await sleep(80);
  assert.deepEqual(titles(), ['常驻错误'], '常驻提示被暂停/恢复逻辑误删');
});

test('同屏最多 4 条，被挤掉的旧条目连计时器一起清掉', async (t) => {
  t.after(clearAll);
  clearAll();
  for (const n of ['1', '2', '3', '4', '5']) toast.ok(n, undefined, 30);
  assert.deepEqual(titles(), ['2', '3', '4', '5'], '同屏上限不是 4 条（丢最旧）');
  await sleep(120);
  assert.deepEqual(titles(), [], '被挤掉或已到期的条目残留');
});

test('ToastHost 必须把「悬停/聚焦暂停」接到宿主上（否则上面的能力没人用）', () => {
  const src = readFileSync(path.join(SRC, 'components/base/ToastHost.vue'), 'utf8');
  for (const binding of ['@mouseenter="pause"', '@mouseleave="resume"', '@focusin="pause"', '@focusout="onFocusOut"']) {
    assert.ok(src.includes(binding), `ToastHost 缺少 ${binding}`);
  }
  // 焦点在提示区**内部**两个元素之间跳转时不应误判为「离开」
  assert.match(src, /stack\.value\.contains\(next\)/, 'focusout 没有判断焦点是否仍在提示区内');
  // 读屏要能听到：live region 必须**先存在**（不能随消息一起渲染出来）
  assert.match(src, /aria-live="polite"/, '缺少 aria-live，动态提示不会被读屏播报');
  assert.ok(!/v-if="items\.length"/.test(src), 'live region 不能跟着消息一起挂载（那样往往不会被播报）');
});
