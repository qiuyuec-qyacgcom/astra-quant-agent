# Astra Quant Agent 代码审查报告

> 审查时间：2026-10-17
> 审查范围：全项目（astra_backend 25873 行、scripts 29621 行、astra_gateway 1730 行、frontend 188 文件）
> 审查方式：静态代码审查 + 关键发现交叉核实

---

## 一、总览

| 严重程度 | 数量 | 说明 |
|---|---|---|
| 🔴 严重（资金损失/安全漏洞/崩溃） | 5 | 需立即修复 |
| 🟠 中等（逻辑错误/数据不一致） | 9 | 建议尽快修复 |
| 🟡 轻微（代码质量/潜在风险） | 14 | 择机修复 |
| **合计** | **28** | |

---

## 二、🔴 严重问题

### 1. 会话令牌明文拼入 URL 查询参数（安全漏洞）

- **文件**：`frontend/src/views/admin/BackupPage.vue:205-206`
- **问题**：`downloadArchive` 把会话令牌拼进下载链接的 `?token=` 查询参数。
- **后果**：令牌进入后端访问日志、反向代理日志、浏览器历史，并通过 `Referer` 头泄露给第三方。任何能读日志的人可窃取管理员会话，接管整个量化系统（含实盘平仓、资金操作权限）。

### 2. 浮点杠杆被 `int()` 静默截断（资金安全边界）

- **文件**：`scripts/trader/order_submit.py:392`，`scripts/trader/entry_execution.py:362/381/497/516`
- **问题**：`int(_want_lever)` 对浮点杠杆（如 3.5x）向下取整为 3，而非四舍五入。
- **后果**：AI 裁决 3.5x 时，实际杠杆档设为 3x，与张数/保证金折算脱节，导致实际保证金占用与风控模型（强平价计算）不一致；通知文案同样谎报杠杆。

### 3. 云端止损修正失败仍更新本地追踪器（状态分叉，资金安全核心风险）

- **文件**：`scripts/trader/position_exit.py:322、377`
- **问题**：`sync_cloud_algo_stop(...)` 返回 bool 表示云端 OCO 修正是否成功，但**返回值被忽略**——无论云端是否成功，本地 `t["trailingStopPx"]` 都已先写入新值。
- **后果**：云端修正失败时（网络抖动、算法单被拒、仓位模式不匹配），本地认为止损已推进到更紧的保本价，云端实际仍停在旧价。行情反向时按旧止损平仓，损失大于本地预期。这是唯一直接触及资金安全防护失效语义的缺陷。

### 4. Claude 响应 `usage` 为 null 时崩溃

- **文件**：`astra_backend/llm/transport.py:71`
- **问题**：`res_json.get("usage", {}).get("input_tokens", 0)` —— 当 JSON 中 `usage` 字段显式为 `null` 时，`.get("usage", {})` 返回 `None`（默认值不生效），`None.get(...)` 抛 `AttributeError`。
- **后果**：Claude 模型无 usage 上报时解析阶段崩溃，被上层当作瞬时故障重试/回退，浪费请求窗口；若所有候选模型都走此路径，本轮 LLM 推演整体失败，交易周期降级为 NO_CHANGE。

### 5. 网关 shutdown 不等待子进程（交易作业被强制中断）

- **文件**：`astra_gateway/scheduler.py:188-189`
- **问题**：`self.executor.shutdown(wait=False, cancel_futures=False)` —— SIGTERM 后进程立即结束，线程池里正在 `subprocess.run` 执行的 trader 子进程（超时长达 1260s）被连带终止。
- **后果**：worker 重启/看护 kill 时，正在运行的 `ai_factor_trader.py` 被半路杀死。若已下单但未写回决策/账本文件，产生决策文件与持仓不一致；半成品决策文件（JSON 写一半）会被后续周期读到损坏数据。

---

## 三、🟠 中等问题

### 6. 分批止盈文案硬编码 "50%"，与可配置比例不符

- **文件**：`scripts/trader/scale_out.py:260、290`
- **问题**：`SCALE_OUT_RATIO` 可配置（默认 0.5），同函数 269/418/421 行都正确用动态比例，但 `stage_desc`（260 行）和 `side_suffix`（290 行）写死 "50%"。
- **后果**：用户把比例调成 35% 或 60% 时，持仓追踪器和台账字段显示错误的 "50%"，与实际成交比例矛盾，误导对实际持仓规模的判断。

### 7. `target_rr` 硬编码 1.6 地板，三处 R:R 下限不一致

- **文件**：`astra_backend/risk_config.py:405`
- **问题**：`max(1.6, ...)` 硬地板与 schema 允许的 1.0、`order_risk.py:68` 的 `ABS_MIN_RR = 1.2` 三处下限互不一致。
- **后果**：管理员设最小盈亏比为 1.2 时，提示词注入的 `target_rr` 仍显示 1.6，提示词口径与代码口径分裂，可能引导模型放弃 1.2~1.6 区间本可成交的机会。

### 8. 一次性平仓令牌先消费后校验（竞态 + 可用性）

- **文件**：`astra_backend/okx_trade_service.py:49-53`
- **问题**：`_consume_intent` 先把 token 从字典 `pop` 掉，之后才校验环境 ID 和确认短语。
- **后果**：用户输错一次确认短语或环境恰好切换，token 已被消费，必须重新刷新持仓重新生成。在"应急平仓"这种紧张场景下，拖慢人工止损速度。

### 9. 台账同步浮点转换无异常保护，脏数据拖垮整批

- **文件**：`scripts/db_manager.py:193、203、205-207、264`
- **问题**：`_normalize_row` 中多处 `float(...)` 无 `try/except`，`sync_json_to_sqlite` 是批量循环。
- **后果**：单条脏数据（如 `"N/A"`）导致整个同步流程在 `commit()` 前抛异常中断，尚未提交的所有行全部丢失，SQLite 与 JSON 双写账本一致性被破坏。

### 10. 登录响应无结构校验，`undefined` 被序列化进 localStorage

- **文件**：`frontend/src/stores/auth.ts:36-39`
- **问题**：`token.value = data.session_token` 无校验，后端结构变化时 `undefined` 被 `localStorage.setItem` 写入字符串 `"undefined"`；`role` 缺省 `'admin'`。
- **后果**：`restoreSession` 读到 `"undefined"` 视为有效 token，`isAuthenticated` 为 true，路由守卫放行，但所有请求 401——"看似已登录、实际全失效"的诡异状态。

### 11. `loading` ref 从未置 true（重试按钮失效 + 并发请求）

- **文件**：`frontend/src/stores/dashboard.ts:133-169`
- **问题**：`loading.value` 只在 `finally` 置 false，初始也是 false，全程无 true。
- **后果**：重试按钮在请求进行中不禁用、不显示 loading；用户连点触发多个并发大接口请求，弱网下加剧竞态与卡顿。

### 12. `toggleProviderQuick` 乐观更新后刷新失败导致状态漂移

- **文件**：`frontend/src/composables/useLlmConfig.ts:333-345`
- **问题**：先乐观改 `p.enabled`，再 `loadConfig()`；若 `loadConfig` 抛错，catch 只 toast，状态已漂移。
- **后果**：界面显示供应商已启用/停用，但刷新后跳回真实值——"开关点一下、刷新后又跳回去"。

### 13. 网关投递详情永久丢失（`accepted` 分支为死代码）

- **文件**：`astra_gateway/store.py:178` + `worker.py`
- **问题**：`detail[:1000] if status == "accepted" else ""` —— 但 channel 的 `status` 默认值恒为 `"delivered"`，`accepted` 分支永不触发。
- **后果**：成功投递的渠道回执（webhook message_id、QQ message_id 等可追踪信息）全部丢弃，运维排查投递问题无从对账。

### 14. 回测指标 `max_dd_avg` 命名误导（取 max 而非 avg）

- **文件**：`scripts/backtest/metrics.py:207`
- **问题**：`max(res["max_drawdown_pct"] ...)` 取最差单资产回撤，变量名却是 `avg`。
- **后果**：组合回测报告的 `max_drawdown_pct` 实为"最差单资产回撤"，被系统性高估，风险画像偏保守失真。

---

## 四、🟡 轻微问题

### 15. 台账写入类型检查顺序颠倒

- **文件**：`scripts/trader/ledger_writer.py:68-69`
- **问题**：`trade_data.setdefault(...)` 先调用 dict 方法，`isinstance` 检查在后。
- **后果**：非 dict 输入在类型保护之前就抛 `AttributeError`，类型检查形同虚设。

### 16. 自进化引擎直接下标访问 `t["net_pnl"]`

- **文件**：`scripts/self_improvement_engine.py:1081-1086`
- **问题**：台账记录用 `t["net_pnl"]` 直接访问，无 `.get()` 兜底。
- **后果**：脏数据/旧版本记录缺键时抛 `KeyError`，自我改进流程中断，且后台异步任务可能静默失败。

### 17. 极小额账户单笔风险被硬抬高到 0.05

- **文件**：`astra_backend/execution/sizing.py:18`、`scripts/risk_constants.py:199`
- **问题**：`max(..., 0.05)` 强制最小风险额。
- **后果**：账户可用余额极小（< 2.5 USDT）时，单笔风险比例被放大，比例风控失真。

### 18. 审计日志读写无锁

- **文件**：`astra_backend/audit.py:46-57`
- **问题**：`recent` 用 `read_text` 读，追加写无锁。
- **后果**：极端并发下读到半行，`JSONDecodeError` 被 `continue` 跳过导致偶发漏掉正在写入的记录（不崩溃）。

### 19. 登录守卫驱逐排序键语义含混

- **文件**：`astra_backend/login_guard.py:80`
- **问题**：空 `attempts` 用 `0` 兜底参与排序。
- **后果**：仅失败未尝试的 IP 被优先驱逐，逻辑可运行但排序键混杂两类状态。

### 20. `.env` 写入中间插入空行

- **文件**：`astra_backend/settings_store.py:186-189`
- **问题**：追加新键前无条件 `result.append("")`。
- **后果**：写出格式不干净，逻辑脆弱易在重构中引入键值粘连。

### 21. 图表实例挂到全局 window

- **文件**：`frontend/src/views/ChartWorkstation.vue:605`
- **问题**：`(window as any).__klineChart = klineChart`。
- **后果**：全局命名空间污染，组件卸载后成为悬垂引用，疑似调试遗留。

### 22. 轮询定时器无 effect scope 时永不清理

- **文件**：`frontend/src/composables/useResource.ts:96-98`
- **问题**：`getCurrentScope()` 为 null 时不注册清理。
- **后果**：内存泄漏，定时器持续触发请求。

### 23. 入场价字符串 "0" 回退缺陷

- **文件**：`frontend/src/composables/chartLiveLevels.ts:24`
- **问题**：`position.avgPx || price` 中 `"0"` 是真值，不会回退到当前价。
- **后果**：入场价显示 0，试算面板风险/盈亏比全为 0，误导判断。

### 24. supervisor 守护线程无异常保护

- **文件**：`astra_gateway/supervisor.py:127`
- **问题**：`_run` 循环 `ensure_worker()` 抛异常会使 daemon 线程永久死亡。
- **后果**：网关失去自愈能力，worker 崩溃后彻底停摆。

### 25. 数据库 WAL/SHM 文件权限收紧存在 TOCTOU

- **文件**：`astra_gateway/store.py:111`、`secrets.py:49`
- **问题**：`-wal`/`-shm` 文件在首次写时才创建，`chmod` 只对已存在文件生效。
- **后果**：umask 宽松时短暂窗口内 WAL 含敏感内容可被同机其他用户读取。

### 26. 密钥白名单外的自定义 key 被静默丢弃

- **文件**：`astra_gateway/secrets.py:24、97`
- **问题**：`_decrypt_store` 过滤白名单外 key，RMW 会丢弃自定义 key。
- **后果**：自定义/未登记密钥在下次保存时被静默清除。

### 27. `complete()` 异常路径掩盖原始投递异常

- **文件**：`astra_gateway/worker.py:197`
- **问题**：`except Exception` 里 `store.fail` 若抛异常，会冒泡退出 run 循环，且异常信息连日志都没写。
- **后果**：磁盘/SQLite 故障时 worker 静默崩溃，根因丢失。

### 28. 倒计时 NaN 边界

- **文件**：`frontend/src/views/ChartWorkstation.vue:735`、`format.ts:112`
- **问题**：`fmtClock` 解析失败返回 `'--'`，解构 `Number('--')` 得 NaN，默认值只对 undefined 生效。
- **后果**：理论边界，`new Date()` 总能解析，极难触发。

---

## 五、修复优先级建议

| 优先级 | 问题 | 理由 |
|---|---|---|
| P0（立即） | #1 token 泄漏 URL | 安全漏洞，影响所有管理员会话 |
| P0（立即） | #3 云端止损状态分叉 | 资金安全防护失效 |
| P1（尽快） | #2 杠杆截断 | 风控模型一致性 |
| P1（尽快） | #4 Claude usage 崩溃 | LLM 链稳定性 |
| P1（尽快） | #5 gateway shutdown | 交易作业中断 |
| P2（择机） | 其余中等问题 | 数据一致性/可用性 |
| P3（低） | 轻微问题 | 代码质量 |

---

## 六、总体评价

本代码库整体工程质量**较高**：核心交易路径体现了明显的防御性设计（fail-closed 门控、margin 衍生下单、原子 JSON 写入、fcntl 进程锁、SQLite↔JSON 身份轴 upsert、云端 OCO 覆盖校验等），且大量审计注释表明此前已修复过许多同类问题。

但仍有 5 个严重问题需要立即处理，其中 **#1（token 泄漏）和 #3（止损状态分叉）** 是最紧迫的两个——一个直接危及系统安全，一个直接危及资金安全。
