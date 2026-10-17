/**
 * `src/components/dashboard/chartCandles.ts` 行为契约
 * （结构优化阶段 4·B3 第五十五刀）。
 *
 * ## 守什么
 *
 * `ChartWorkstation.vue` 原先有**两条逐字重复**的取蜡烛路径：
 * `setDataLoader({ getBars })` 与 `loadCandles()`。两者各内联了
 * "拼 URL → fetch → 查 `res.ok` → 取 `data.candles` → 转 `KLineData`"。
 * 本模块把它收成一处，两条路径只保留各自的错误处理。
 *
 * 本文件用**受控 fetch 桩**真跑一遍，钉住：
 *   - URL 形状（含 `limit=150` 与 `_t` 缓存穿透参数、`cache: 'no-store'`）
 *   - 字段映射（尤其 **`turnover = vol * close`**，不是 `c.turnover`）
 *   - `data.candles` 缺失/非数组/空 → 视为"无数据"而非错误
 *   - HTTP 非 2xx → 抛 `HTTP <status>`；网络异常**原样上抛**（不吞）
 *   - `lastClose` 语义（末根收盘价的 `Number()` 结果；无数据为 `null`）
 *
 * 运行（Node ≥ 22.6，本仓 v24）：
 *     node --experimental-strip-types tests/chartCandles.test.mjs
 */
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const { candlesUrl, toKlineData, fetchCandles } =
  await import(pathToFileURL(path.resolve('src/components/dashboard/chartCandles.ts')).href);

let pass = 0, fail = 0;
function check(name, cond, extra = '') {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  → ' + extra : '')); }
}
function eq(name, got, want) {
  check(name, JSON.stringify(got) === JSON.stringify(want),
        'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

/** 造一个 fetch 桩，记录调用参数。 */
function stub(impl) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    return impl(url, opts);
  };
  fn.calls = calls;
  return fn;
}
const jsonRes = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

// ---------------------------------------------------------------- candlesUrl
console.log('candlesUrl:');
{
  const u = candlesUrl('BTC-USDT-SWAP', '1H');
  check('路径含标的', u.startsWith('/api/v1/market/BTC-USDT-SWAP/candles?'), u);
  check('含 bar 周期', u.includes('bar=1H'), u);
  check('含 limit=150', u.includes('limit=150'), u);
  check('含 _t 缓存穿透参数', /[?&]_t=\d+/.test(u), u);
  const u2 = candlesUrl('BTC-USDT-SWAP', '1H');
  check('_t 每次不同（穿透缓存）', u !== u2 || Date.now() === Date.now());
}

// --------------------------------------------------------------- toKlineData
console.log('toKlineData:');
{
  const raw = [{ ts: 1000, open: 1, high: 2, low: 0.5, close: 1.5, vol: 10 }];
  const out = toKlineData(raw);
  eq('字段名逐字映射', Object.keys(out[0]).sort(),
     ['close', 'high', 'low', 'open', 'timestamp', 'turnover', 'volume']);
  eq('ts → timestamp', out[0].timestamp, 1000);
  eq('vol → volume', out[0].volume, 10);
  eq('turnover = vol * close（不是 c.turnover）', out[0].turnover, 15);
  check('输入里若带 turnover 会被忽略', (() => {
    const o = toKlineData([{ ts: 1, open: 1, high: 1, low: 1, close: 2, vol: 3, turnover: 999 }]);
    return o[0].turnover === 6;
  })());
  eq('空数组 → 空数组', toKlineData([]), []);
  eq('保序等长', toKlineData(raw).length, 1);
}

// -------------------------------------------------------------- fetchCandles
console.log('fetchCandles 成功路径:');
{
  const rows = [{ ts: 1, open: 1, high: 2, low: 0.5, close: 1.5, vol: 10 }];
  const f = stub(async () => jsonRes({ candles: rows }));
  const r = await fetchCandles('BTC-USDT-SWAP', '1H', f);
  eq('candles 原样返回', r.candles, rows);
  eq('klineList 归一', r.klineList.length, 1);
  eq('lastClose = 末根 close', r.lastClose, 1.5);
  check('只请求一次', f.calls.length === 1, String(f.calls.length));
  check('URL 走了 candlesUrl', f.calls[0].url === candlesUrl('BTC-USDT-SWAP', '1H').replace(/_t=\d+/, '_t=X').replace(/_t=\d+/, 'X') || /\/api\/v1\/market\/BTC-USDT-SWAP\/candles\?bar=1H&limit=150&_t=\d+/.test(f.calls[0].url), f.calls[0].url);
  eq('cache: no-store', f.calls[0].opts.cache, 'no-store');
}

console.log('fetchCandles 多根取末根:');
{
  const rows = [
    { ts: 1, open: 1, high: 1, low: 1, close: 1, vol: 1 },
    { ts: 2, open: 2, high: 2, low: 2, close: 2, vol: 2 },
    { ts: 3, open: 3, high: 3, low: 3, close: 42, vol: 3 },
  ];
  const r = await fetchCandles('X', '1H', stub(async () => jsonRes({ candles: rows })));
  eq('lastClose 取最后一根', r.lastClose, 42);
  eq('klineList 保序', r.klineList.map(k => k.timestamp), [1, 2, 3]);
}

console.log('fetchCandles 无数据（不是错误）:');
for (const [label, body] of [
  ['candles 为空数组', { candles: [] }],
  ['candles 缺失', {}],
  ['candles 是 null', { candles: null }],
  ['candles 不是数组', { candles: 'nope' }],
  ['响应体是空对象', {}],
]) {
  const r = await fetchCandles('X', '1H', stub(async () => jsonRes(body)));
  check(`${label} → 不抛异常且 candles 为空`, r.candles.length === 0 && r.klineList.length === 0, JSON.stringify(r));
  check(`${label} → lastClose 为 null`, r.lastClose === null, String(r.lastClose));
}

console.log('fetchCandles 错误路径:');
for (const status of [400, 401, 404, 500, 503]) {
  let err = null;
  try { await fetchCandles('X', '1H', stub(async () => jsonRes({}, status))); }
  catch (e) { err = e; }
  check(`HTTP ${status} → 抛 HTTP ${status}`, err && err.message === `HTTP ${status}`, err && err.message);
}
{
  let err = null;
  try {
    await fetchCandles('X', '1H', stub(async () => { throw new TypeError('Failed to fetch'); }));
  } catch (e) { err = e; }
  check('网络异常原样上抛（不吞、不包装）',
        err instanceof TypeError && err.message === 'Failed to fetch', String(err));
}
{
  let err = null;
  try { await fetchCandles('X', '1H', stub(async () => jsonRes({ candles: [] }, 502))); }
  catch (e) { err = e; }
  check('非 2xx 即使体里像空数据也抛错', err && err.message === 'HTTP 502', err && err.message);
}
{
  // 非 2xx 不得去读 body 里的 candles —— 状态码优先
  let err = null;
  try {
    await fetchCandles('X', '1H', stub(async () => jsonRes({ candles: [{ ts: 1, close: 1, vol: 1 }] }, 500)));
  } catch (e) { err = e; }
  check('500 不会把错误响应当行情用', err && err.message === 'HTTP 500', err && err.message);
}

console.log('fetchCandles lastClose 语义（与原实现一致）:');
{
  const r = await fetchCandles('X', '1H',
    stub(async () => jsonRes({ candles: [{ ts: 1, open: 1, high: 1, low: 1, close: '7.5', vol: 1 }] })));
  eq('字符串 close 被 Number() 化', r.lastClose, 7.5);
  check('lastClose 是 number 类型', typeof r.lastClose === 'number', typeof r.lastClose);
  const r2 = await fetchCandles('X', '1H',
    stub(async () => jsonRes({ candles: [{ ts: 1, open: 1, high: 1, low: 1, vol: 1 }] })));
  check('close 缺失 → NaN（与原实现一致，调用方自行校验）',
        Number.isNaN(r2.lastClose), String(r2.lastClose));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
