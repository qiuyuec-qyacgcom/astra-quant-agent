/**
 * `src/utils/format.ts` 行为契约（批 20）。
 *
 * ## 守什么
 *
 * `format.ts` 是全站**唯一**的数字/时间格式化实现（前端 README 明示），
 * 台账、持仓、挂单、矩阵、雷达、抽屉都消费它。它此前没有测试，
 * 所以"缺数据长什么样"这种跨页一致的细节没有任何东西钉住。
 *
 * 本文件钉住三条**看起来可优化、其实不能动**的规则：
 *
 *   1. `fmtPrice(0) === '--'`（批 20 变更）
 *      0 在加密资产里只可能是"没有这个数"，不是"价格为 0"。
 *      旧实现会输出 `0.000000`（abs<0.01 走 6 位档），在台账里像真值。
 *      注意 `--` 只对 **0** 生效；`0.000001` 这种真实极小价仍要正常输出。
 *
 *   2. 精度档边界是**闭区间**，档位不能"顺手取整"：
 *      ≥1000→2 位、≥10→3、≥1→4、≥0.01→5、其余→6。边界值 1000/10/1/0.01
 *      都属于**下一档**（>= 而不是 >），改一个符号全站精度就变。
 *
 *   3. `fmtSigned(0)` 输出不带符号的 `0.00`（不是 `+0.00`）：
 *      台账里"零盈亏"和"盈利"必须一眼可分。
 *
 * 运行（Node ≥ 22.6，本仓 v24）：
 *     node --experimental-strip-types tests/format.test.mjs
 */
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const { fmtNum, fmtSigned, fmtPct, fmtPrice, fmtCompact, dirClass } = await import(
  pathToFileURL(path.resolve('src/utils/format.ts')).href
);

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass++;
    console.log('  ok   ' + name);
  } else {
    fail++;
    console.log('  FAIL ' + name + (extra ? '  → ' + extra : ''));
  }
}

console.log('\nfmtPrice · 0 视为无值（批 20）');
check("fmtPrice(0) === '--'", fmtPrice(0) === '--', fmtPrice(0));
check("fmtPrice('0') === '--'", fmtPrice('0') === '--', fmtPrice('0'));
check("fmtPrice(-0) === '--'", fmtPrice(-0) === '--', fmtPrice(-0));
check("fmtPrice(null) === '--'", fmtPrice(null) === '--');
check("fmtPrice(undefined) === '--'", fmtPrice(undefined) === '--');
check("fmtPrice('abc') === '--'", fmtPrice('abc') === '--');
check("fmtPrice(NaN) === '--'", fmtPrice(NaN) === '--');
check("fmtPrice(Infinity) === '--'", fmtPrice(Infinity) === '--');
check('极小真实价不被当成无值', fmtPrice(0.000001) === '0.000001', fmtPrice(0.000001));

console.log('\nfmtPrice · 精度档边界（闭区间）');
check('1000 → 2 位', fmtPrice(1000) === '1,000.00', fmtPrice(1000));
check('999.9 → 3 位', fmtPrice(999.9) === '999.900', fmtPrice(999.9));
check('10 → 3 位', fmtPrice(10) === '10.000', fmtPrice(10));
check('9.99 → 4 位', fmtPrice(9.99) === '9.9900', fmtPrice(9.99));
check('1 → 4 位', fmtPrice(1) === '1.0000', fmtPrice(1));
check('0.99 → 5 位', fmtPrice(0.99) === '0.99000', fmtPrice(0.99));
check('0.01 → 5 位', fmtPrice(0.01) === '0.01000', fmtPrice(0.01));
check('0.009 → 6 位', fmtPrice(0.009) === '0.009000', fmtPrice(0.009));
check('字符串数字同样走价格档', fmtPrice('2460.8') === '2,460.80', fmtPrice('2460.8'));
check('负数走绝对值分档', fmtPrice(-99.5) === '-99.500', fmtPrice(-99.5));

console.log('\nfmtNum / fmtSigned / fmtPct');
check("fmtNum(null) === '--'", fmtNum(null) === '--');
check('fmtNum 默认 2 位 + 千分位', fmtNum(1234567.891) === '1,234,567.89', fmtNum(1234567.891));
check('fmtNum 固定位数（不做四舍五入省略）', fmtNum(3, 4) === '3.0000', fmtNum(3, 4));
check("fmtSigned 正数带 +", fmtSigned(15.85) === '+15.85', fmtSigned(15.85));
check("fmtSigned 负数带 -", fmtSigned(-3.2) === '-3.20', fmtSigned(-3.2));
check("fmtSigned(0) 不带符号", fmtSigned(0) === '0.00', fmtSigned(0));
check('fmtPct 正数带 +', fmtPct(1.01) === '+1.01%', fmtPct(1.01));
check('fmtPct 负数不带多余 +', fmtPct(-5.42) === '-5.42%', fmtPct(-5.42));
check('fmtPct 可关掉符号', fmtPct(1.5, 2, false) === '1.50%', fmtPct(1.5, 2, false));

console.log('\nfmtCompact / dirClass');
check('1.2M 缩写', fmtCompact(1234567) === '1.2M', fmtCompact(1234567));
check('345.7K 缩写', fmtCompact(345678) === '345.7K', fmtCompact(345678));
check('小数保留 2 位', fmtCompact(12.345) === '12.35', fmtCompact(12.345));
check('dirClass 0 无方向', dirClass(0) === '', dirClass(0));
check('dirClass 正为 up', dirClass(1) === 'up');
check('dirClass 负为 down', dirClass(-1) === 'down');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
