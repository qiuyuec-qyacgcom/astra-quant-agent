<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { Download, RefreshCw, GitCompareArrows } from 'lucide-vue-next';
import { get, post, del, HttpError } from '../../api/http';
import { useI18n } from '../../composables/useI18n';
import { useToast } from '../../composables/useToast';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseStat from '../../components/base/BaseStat.vue';
import BaseTabs from '../../components/base/BaseTabs.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import BasePager from '../../components/base/BasePager.vue';
import BaseDrawer from '../../components/base/BaseDrawer.vue';
import BaseCodeBlock from '../../components/base/BaseCodeBlock.vue';

type RecordData = Record<string, any>;
interface Trade extends RecordData { id: string; inst: string; side: string; cost_complete: boolean; net_pnl: number | string | null }
interface Event extends RecordData { id: string; kind: string; status: string; occurred_ms: number; inst: string }
interface Config extends RecordData { id: string; source: string; process: string; captured_ms: number }
const { t, locale } = useI18n();
const tr = (key: string) => t(`analysis.${key}`);
const toast = useToast();
const api = '/api/v1/admin/analysis';
const summary = ref<RecordData | null>(null);
const loading = ref(false), exporting = ref(false), error = ref('');
interface ExportJob {
  id: string; state: 'running' | 'cancelling' | 'ready' | 'failed' | 'cancelled';
  stage: string; completed: number; total: number; bytes: number; error: string;
}
const exportJob = ref<ExportJob | null>(null), exportError = ref(''), downloading = ref(false);
const exportStorageKey = 'astra.analysis.export';
let exportTimer: ReturnType<typeof setTimeout> | undefined, disposed = false;
const tab = ref('overview');
const bjToday = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
const startDate = ref(new Date(Date.parse(bjToday) - 29 * 86400000).toISOString().slice(0, 10));
const endDate = ref(bjToday), account = ref(''), inst = ref(''), side = ref(''), configuration = ref(''), outcome = ref('');
const groupBy = ref('inst'), groups = ref<RecordData[]>([]);
const trades = ref<Trade[]>([]), tradeTotal = ref(0), tradePage = ref(1);
const events = ref<Event[]>([]), eventTotal = ref(0), eventPage = ref(1), applied = ref('');
const tradeDetail = ref<{ trade: Trade; events: Event[]; config_ids: string[] } | null>(null);
const evidence = ref<Event | null>(null), drawerOpen = ref(false), detailBusy = ref(false);
const leftId = ref(''), rightId = ref('current'), leftConfig = ref<Config | null>(null), rightConfig = ref<Config | null>(null);
let sequence = 0, detailSequence = 0, configSequence = 0;
let tradeSequence = 0, eventSequence = 0, groupSequence = 0;
const tabs = computed(() => ['overview', 'attribution', 'configurations', 'execution'].map(key => ({ key, label: tr(key) })));
const dimensions = ['inst', 'side', 'config_id', 'confidence', 'duration', 'exit_reason'];
const eventLabel = (kind: string) => {
  const key = ({ 'llm.request': 'modelRequest', 'llm.response': 'modelResponse', 'decision.proposed': 'proposed', 'decision.filtered': 'riskDecision', 'order.submitted': 'submitted', 'order.fill': 'filled', 'order.adjusted': 'orderAdjustment', 'position.exit_reason': 'exitReason', 'position.sample': 'positionSample', 'risk.rule': 'riskRule' } as Record<string,string>)[kind];
  return key ? tr(key) : kind;
};
const dimensionLabel = (key: string) => tr(({ config_id: 'config', exit_reason: 'exitReason' } as Record<string,string>)[key] || key);
const stats = computed(() => summary.value?.statistics || {});
const execution = computed(() => summary.value?.execution || {});
const health = computed(() => summary.value?.health || {});
const configOptions = computed<Config[]>(() => summary.value?.configurations || []);
const accounts = computed<string[]>(() => Array.from(new Set([account.value, summary.value?.account, ...(summary.value?.accounts || [])])).filter(Boolean) as string[]);
const format = (v: unknown, decimals = 2) => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString(locale.value, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
const pct = (v: unknown) => v == null ? '—' : format(v, 1) + '%';
const time = (v: number | null | undefined) => v ? new Date(v).toLocaleString(locale.value, { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
const json = (v: unknown) => JSON.stringify(v, null, 2) ?? '—';
const color = (v: unknown) => v == null ? '' : Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : '';
const metrics = computed(() => [
  ['net', stats.value.net_pnl], ['pf', stats.value.profit_factor], ['payoff', stats.value.payoff_ratio],
  ['avgWin', stats.value.avg_win], ['avgLoss', stats.value.avg_loss], ['expectancy', stats.value.expectancy],
  ['streak', stats.value.longest_loss_streak], ['drawdown', stats.value.max_realized_drawdown],
  ['fees', stats.value.fee], ['funding', stats.value.funding_fee],
] as const);
const fault = computed(() => Boolean(health.value.capture_fault && Object.keys(health.value.capture_fault).length) || health.value.sync?.some((x: RecordData) => x.error));
const pending = computed(() => !health.value.sync?.length || health.value.sync.some((x: RecordData) => !x.complete));
const syncProblems = computed(() => (health.value.sync || []).filter((x: RecordData) => x.error).map((x: RecordData) => `${x.source}: ${x.error}`));

function params() {
  if (!startDate.value || !endDate.value || endDate.value < startDate.value) throw new Error(tr('start') + ' / ' + tr('end'));
  const exclusive = new Date(Date.parse(endDate.value) + 86400000).toISOString().slice(0, 10);
  return new URLSearchParams({ account: account.value, start: startDate.value, end: exclusive, inst: inst.value, side: side.value, config_id: configuration.value, result: outcome.value }).toString();
}
async function apply(reset = true) {
  const id = ++sequence;
  const requestedTab = tab.value;
  const requestedGroup = groupBy.value;
  ++tradeSequence; ++eventSequence; ++groupSequence; ++detailSequence; ++configSequence;
  drawerOpen.value = false;
  tradeDetail.value = null; evidence.value = null;
  leftConfig.value = null; rightConfig.value = null;
  loading.value = true; error.value = '';
  try {
    const query = params();
    if (reset) { tradePage.value = 1; eventPage.value = 1; }
    const [s, rows, grouped, history] = await Promise.all([
      get<RecordData>(api + '/summary?' + query),
      get<{ items: Trade[]; total: number }>(api + '/trades?' + query + '&page=' + tradePage.value),
      requestedTab === 'attribution' ? get<{ items: RecordData[] }>(api + '/breakdown?' + query + '&by=' + requestedGroup) : Promise.resolve({ items: [] }),
      requestedTab === 'execution' ? get<{ items: Event[]; total: number }>(api + '/events?' + query + '&page=' + eventPage.value) : Promise.resolve({ items: [], total: 0 }),
    ]);
    if (id !== sequence) return;
    summary.value = s; account.value = s.account;
    const fixed = new URLSearchParams(query); fixed.set('account', s.account); applied.value = fixed.toString();
    trades.value = rows.items; tradeTotal.value = rows.total; groups.value = grouped.items;
    events.value = history.items; eventTotal.value = history.total;
    if (!configOptions.value.some(c => c.id === leftId.value)) leftId.value = configOptions.value[0]?.id || '';
    if (tab.value === 'configurations') void compare();
  } catch (e) { if (id === sequence) error.value = e instanceof Error ? e.message : String(e); }
  finally {
    if (id === sequence) {
      loading.value = false;
      if (!error.value) {
        if (tab.value === 'attribution' && (requestedTab !== tab.value || requestedGroup !== groupBy.value)) void loadGroups();
        if (tab.value === 'execution' && requestedTab !== tab.value) void changePage('event', eventPage.value);
      }
    }
  }
}
async function changePage(kind: 'trade' | 'event', page: number) {
  if (!applied.value || loading.value) return;
  const query = applied.value, revision = sequence;
  const request = kind === 'trade' ? ++tradeSequence : ++eventSequence;
  try {
    if (kind === 'trade') {
      const r = await get<{ items: Trade[]; total: number }>(api + '/trades?' + query + '&page=' + page);
      if (query !== applied.value || revision !== sequence || request !== tradeSequence) return;
      trades.value = r.items; tradeTotal.value = r.total; tradePage.value = page;
    } else {
      const r = await get<{ items: Event[]; total: number }>(api + '/events?' + query + '&page=' + page);
      if (query !== applied.value || revision !== sequence || request !== eventSequence) return;
      events.value = r.items; eventTotal.value = r.total; eventPage.value = page;
    }
  } catch (e) { toast.err(String(e)); }
}
async function loadGroups() {
  if (!applied.value || loading.value) return;
  const query = applied.value, by = groupBy.value, revision = sequence, request = ++groupSequence;
  try {
    const r = await get<{ items: RecordData[] }>(api + '/breakdown?' + query + '&by=' + by);
    if (query === applied.value && by === groupBy.value && revision === sequence && request === groupSequence) groups.value = r.items;
  } catch (e) { toast.err(String(e)); }
}
async function inspectTrade(id: string) {
  const seq = ++detailSequence;
  drawerOpen.value = true; detailBusy.value = true; tradeDetail.value = null; evidence.value = null;
  try {
    const d = await get<{ trade: Trade; events: Event[]; config_ids: string[] }>(api + '/trades/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || ''));
    if (seq === detailSequence) tradeDetail.value = d;
  } catch (e) { toast.err(String(e)); } finally { if (seq === detailSequence) detailBusy.value = false; }
}
async function inspectEvent(id: string, keepTrade = false) {
  const seq = ++detailSequence;
  drawerOpen.value = true; detailBusy.value = true; evidence.value = null;
  if (!keepTrade) tradeDetail.value = null;
  try {
    const d = await get<Event>(api + '/events/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || ''));
    if (seq === detailSequence) evidence.value = d;
  } catch (e) { toast.err(String(e)); } finally { if (seq === detailSequence) detailBusy.value = false; }
}
async function compare() {
  const seq = ++configSequence;
  leftConfig.value = null; rightConfig.value = null;
  if (!leftId.value) return;
  try {
    const url = (id: string) => api + '/configurations/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || '');
    const [left, right] = await Promise.all([get<Config>(url(leftId.value)), get<Config>(url(rightId.value))]);
    if (seq === configSequence) { leftConfig.value = left; rightConfig.value = right; }
  } catch (e) { toast.err(String(e)); }
}
function flatten(v: unknown, prefix = '', out: Record<string,string> = {}) {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    for (const [k, x] of Object.entries(v)) flatten(x, prefix ? prefix + '.' + k : k, out);
  } else out[prefix] = json(v);
  return out;
}
const differences = computed(() => {
  if (!leftConfig.value || !rightConfig.value) return [];
  const a = flatten(leftConfig.value.body), b = flatten(rightConfig.value.body);
  return Array.from(new Set([...Object.keys(a), ...Object.keys(b)])).sort().filter(k => a[k] !== b[k]).map(key => ({ key, before: a[key] ?? '—', after: b[key] ?? '—' }));
});
function rememberExport(id: string | null) {
  try {
    if (id) sessionStorage.setItem(exportStorageKey, id);
    else sessionStorage.removeItem(exportStorageKey);
  } catch { /* Storage may be unavailable in private browsing. */ }
}
const exportProgress = computed(() => {
  const job = exportJob.value;
  if (!job) return '';
  const stage = job.stage.startsWith('facts.') ? 'facts' : job.stage;
  const label = tr('exportStage_' + stage);
  return label + (job.total ? ` · ${job.completed.toLocaleString()} / ${job.total.toLocaleString()}`
    : job.completed ? ` · ${job.completed.toLocaleString()}` : '');
});
async function downloadExport() {
  if (!exportJob.value || downloading.value) return;
  downloading.value = true;
  try {
    const result = await post<{ url: string }>(api + '/exports/' + exportJob.value.id + '/download');
    if (disposed) return;
    const link = document.createElement('a');
    link.href = result.url;
    link.download = 'astra-analysis-' + bjToday + '.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    try { sessionStorage.setItem(exportStorageKey + '.downloaded', exportJob.value.id); } catch { /* optional */ }
    exportError.value = '';
    toast.ok(tr('downloadStarted'));
  } catch (e) { exportError.value = e instanceof Error ? e.message : String(e); }
  finally { downloading.value = false; }
}
function scheduleExportPoll(id: string) {
  if (!disposed) exportTimer = setTimeout(() => { void pollExport(id); }, 1500);
}
async function pollExport(id: string) {
  try {
    const job = await get<ExportJob>(api + '/exports/' + id);
    if (disposed || exportJob.value?.id !== id) return;
    exportJob.value = job;
    exportError.value = '';
    exporting.value = job.state === 'running' || job.state === 'cancelling';
    if (exporting.value) scheduleExportPoll(id);
    else if (job.state === 'ready') {
      let downloaded = false;
      try { downloaded = sessionStorage.getItem(exportStorageKey + '.downloaded') === job.id; } catch { /* optional */ }
      if (!downloaded) await downloadExport();
    }
    else if (job.state === 'failed') exportError.value = tr(job.error === 'disk_full' ? 'exportDiskFull' : 'exportFailed');
  } catch (e) {
    if (disposed) return;
    exportError.value = e instanceof Error ? e.message : String(e);
    if (e instanceof HttpError && [401, 403, 404].includes(e.status)) {
      exporting.value = false;
      exportJob.value = null;
      rememberExport(null);
    } else scheduleExportPoll(id);
  }
}
async function exportAll() {
  if (!applied.value || exporting.value) return;
  clearTimeout(exportTimer);
  exporting.value = true;
  exportError.value = '';
  try {
    const job = await post<ExportJob>(api + '/exports?' + applied.value);
    rememberExport(job.id);
    if (disposed) return;
    exportJob.value = job;
    await pollExport(job.id);
  } catch (e) {
    exporting.value = false;
    exportError.value = e instanceof Error ? e.message : String(e);
  }
}
async function cancelExport() {
  if (!exportJob.value || exportJob.value.state !== 'running') return;
  try { exportJob.value = await del<ExportJob>(api + '/exports/' + exportJob.value.id); }
  catch (e) { exportError.value = e instanceof Error ? e.message : String(e); }
}
type PlotPoint = { time_ms: number; net: number; drawdown: number; trade_id: string; x: number; y: number; dy: number };
const hoveredPoint = ref<PlotPoint | null>(null);
const plot = computed(() => {
  const source: { time_ms: number; net: number; drawdown: number; trade_id: string }[] = stats.value.curve || [];
  const rows = source;
  const values = rows.flatMap(p => [p.net, -p.drawdown]);
  const min = values.reduce((a, b) => Math.min(a, b), 0), max = values.reduce((a, b) => Math.max(a, b), 0), span = max - min || 1;
  const y = (n: number) => 238 - (n - min) / span * 204;
  const points = rows.map((p, i) => ({ ...p, x: 76 + i / Math.max(1, rows.length - 1) * 888, y: y(p.net), dy: y(-p.drawdown) }));
  const ticks = Array.from({ length: 5 }, (_, i) => ({ value: min + span * i / 4, y: y(min + span * i / 4) }));
  const dates = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])].filter(i => i >= 0).map(i => points[i]!);
  return { points, ticks, dates, zero: y(0), min: rows.reduce((n, p) => Math.min(n, p.net), 0), max: rows.reduce((n, p) => Math.max(n, p.net), 0), current: rows.at(-1)?.net ?? null, trades: source.length, line: points.map(p => p.x + ',' + p.y).join(' '), drawdown: points.map(p => p.x + ',' + p.dy).join(' '), area: points.length ? `76,${y(0)} ${points.map(p => `${p.x},${p.dy}`).join(' ')} ${points.at(-1)!.x},${y(0)}` : '' };
});
watch(plot, () => { hoveredPoint.value = null; });
function hoverCurve(event: MouseEvent) {
  const svg = event.currentTarget as SVGSVGElement;
  const matrix = svg.getScreenCTM();
  if (!matrix) return;
  const point = svg.createSVGPoint();
  point.x = event.clientX; point.y = event.clientY;
  const x = point.matrixTransform(matrix.inverse()).x;
  const index = Math.round((x - 76) / 888 * Math.max(0, plot.value.points.length - 1));
  hoveredPoint.value = plot.value.points[Math.max(0, Math.min(plot.value.points.length - 1, index))] ?? null;
}
function stepCurve(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const points = plot.value.points;
  const index = hoveredPoint.value ? points.indexOf(hoveredPoint.value) : -1;
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : index + (event.key === 'ArrowLeft' ? -1 : 1);
  hoveredPoint.value = points[Math.max(0, Math.min(points.length - 1, next))] ?? null;
}
watch(groupBy, loadGroups);
watch([leftId, rightId], () => { if (tab.value === 'configurations') void compare(); });
watch(tab, value => {
  if (value === 'configurations') void compare();
  if (value === 'attribution') void loadGroups();
  if (value === 'execution') void changePage('event', eventPage.value);
});
onMounted(() => {
  void apply();
  try {
    const id = sessionStorage.getItem(exportStorageKey);
    if (id) {
      exportJob.value = { id, state: 'running', stage: 'reading', completed: 0, total: 0, bytes: 0, error: '' };
      exporting.value = true;
      void pollExport(id);
    }
  } catch { /* The export remains usable without browser storage. */ }
});
onUnmounted(() => { disposed = true; clearTimeout(exportTimer); });
</script>

<template>
  <div class="analysis-page space-y-4">
    <PageHeader :title="tr('title')" :description="tr('subtitle')">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="apply(false)"><RefreshCw class="shrink-0" :class="{ 'animate-spin': loading }" />{{ tr('refresh') }}</button>
        <button type="button" class="btn btn-primary btn-sm" :disabled="exporting || loading || !applied" @click="exportAll"><Download />{{ exporting ? tr('exporting') : tr('export') }}</button>
      </template>
    </PageHeader>
    <div v-if="exportJob || exportError" class="card p-3 space-y-2" role="status" aria-live="polite">
      <div class="flex flex-wrap items-center gap-3 text-sm">
        <span v-if="exporting">{{ exportJob?.state === 'cancelling' ? tr('exportCancelling') : exportProgress }}</span>
        <span v-else-if="exportJob?.state === 'ready'">{{ tr('exportReady') }} · {{ format(exportJob.bytes / 1048576) }} MB</span>
        <span v-else-if="exportJob?.state === 'cancelled'">{{ tr('exportCancelled') }}</span>
        <button v-if="exportJob?.state === 'running'" type="button" class="btn btn-ghost btn-sm" @click="cancelExport">{{ tr('exportCancel') }}</button>
        <button v-if="exportJob?.state === 'ready'" type="button" class="btn btn-primary btn-sm" :disabled="downloading" @click="downloadExport">{{ tr('exportDownloadAgain') }}</button>
      </div>
      <p v-if="exporting" class="text-xs t-faint">{{ tr('exportBackgroundHint') }}</p>
      <p v-if="exportJob?.state === 'ready'" class="text-xs t-faint">{{ tr('exportReadyHint') }}</p>
      <p v-if="exportError" role="alert" class="text-xs down">{{ exportError }}</p>
    </div>
    <form class="card analysis-filters" :aria-label="tr('apply')" :aria-busy="loading" @submit.prevent="apply()">
      <label class="analysis-account">{{ tr('account') }}<select v-model="account" class="field" :aria-label="tr('account')"><option v-if="!account" value="">{{ tr('currentAccount') }}</option><option v-for="a in accounts" :key="a" :value="a">{{ a }}</option></select></label>
      <label>{{ tr('start') }}<input v-model="startDate" type="date" required class="field" /></label>
      <label>{{ tr('end') }}<input v-model="endDate" type="date" required class="field" /></label>
      <label>{{ tr('inst') }}<select v-model="inst" class="field" :aria-label="tr('inst')"><option value="">{{ tr('all') }}</option><option v-for="i in summary?.instruments" :key="i" :value="i">{{ i }}</option></select></label>
      <label>{{ tr('side') }}<select v-model="side" class="field" :aria-label="tr('side')"><option value="">{{ tr('all') }}</option><option value="long">{{ tr('long') }}</option><option value="short">{{ tr('short') }}</option></select></label>
      <label class="analysis-outcome">{{ tr('outcome') }}<select v-model="outcome" class="field" :aria-label="tr('outcome')"><option value="">{{ tr('all') }}</option><option value="win">{{ tr('win') }}</option><option value="loss">{{ tr('loss') }}</option><option value="breakeven">{{ tr('breakeven') }}</option></select></label>
      <label class="analysis-configuration">{{ tr('config') }}<select v-model="configuration" class="field" :aria-label="tr('config')"><option value="">{{ tr('all') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }}</option></select></label>
      <button class="btn btn-primary analysis-apply" type="submit" :disabled="loading">{{ tr('apply') }}</button>
    </form>
    <p v-if="error" role="alert" class="card p-3 down">{{ error }}</p>
    <div class="card p-3 space-y-2">
      <div class="flex flex-wrap items-center gap-3 text-xs">
        <strong>{{ tr('health') }}</strong>
        <span>{{ tr('coverage') }}: {{ time(health.coverage_start_ms) }}</span>
        <span>{{ tr('lastSeen') }}: {{ time(health.last_observed_ms) }}</span>
        <span>{{ tr('disk') }}: {{ format((health.bytes || 0) / 1048576) }} MB</span>
        <span>{{ tr('linkRate') }}: {{ pct(stats.decision_link_rate) }}</span>
        <span>{{ tr('costRate') }}: {{ pct(stats.cost_complete_rate) }}</span>
      </div>
      <p v-if="fault" role="status" class="text-xs down">{{ tr('syncError') }}<span v-if="syncProblems.length"> · {{ syncProblems.join('；') }}</span></p>
      <p v-else-if="pending" class="text-xs t-faint">{{ tr('syncPending') }}</p>
      <details v-if="fault || pending" class="text-xs t-faint"><summary>{{ tr('coverageNote') }}</summary><BaseCodeBlock :code="json(health)" /></details>
    </div>
    <BaseTabs v-model="tab" :items="tabs" :label="tr('title')" />
    <p v-if="loading" role="status" class="text-sm t-faint">{{ tr('refresh') }}…</p>
    <section v-if="tab === 'overview'" class="space-y-4">
      <div class="card analysis-stats analysis-stats-summary">
        <BaseStat :label="tr('closed')" :value="format(stats.closed_count, 0)" :delta="tr('incomplete') + ': ' + format(stats.incomplete_count, 0)" />
        <BaseStat :label="tr('complete')" :value="format(stats.sample_count, 0)" :delta="[tr('win') + ' ' + format(stats.wins, 0), tr('loss') + ' ' + format(stats.losses, 0), tr('breakeven') + ' ' + format(stats.breakeven, 0)].join(' / ')" />
        <BaseStat :label="tr('winRate')" :value="pct(stats.win_rate)" />
        <BaseStat :label="tr('confidenceInterval')" :value="stats.win_rate_ci95 ? stats.win_rate_ci95.map(pct).join(' ~ ') : '—'" />
      </div>
      <p class="text-xs t-faint">{{ tr('sampleNote') }}</p>
      <div class="card analysis-stats analysis-stats-metrics"><BaseStat v-for="[key, value] in metrics" :key="key" :label="tr(key)" :value="format(value, key === 'streak' ? 0 : 2)" /></div>
      <div class="card p-4">
        <h2 class="font-semibold text-sm">{{ tr('curve') }}</h2><p class="text-xs t-faint mt-1">{{ tr('curveNote') }}</p>
        <BaseEmpty v-if="!plot.points.length" :text="tr('noData')" />
        <div v-if="plot.points.length" class="curve-summary grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 text-xs"><span><b>{{ tr('minimum') }}</b><strong>{{ format(plot.min) }}</strong></span><span><b>{{ tr('maximum') }}</b><strong>{{ format(plot.max) }}</strong></span><span><b>{{ tr('curveCurrent') }}</b><strong :class="color(plot.current)">{{ format(plot.current) }}</strong></span><span><b>{{ tr('trades') }}</b><strong>{{ format(plot.trades, 0) }}</strong></span></div>
        <div v-if="plot.points.length" class="curve-wrap mt-3">
          <div class="curve-legend"><span><i class="curve-key-net" />{{ tr('curveCurrent') }}</span><span><i class="curve-key-dd" />{{ tr('curveDrawdown') }}</span><span class="curve-unit">USDT</span></div>
          <svg viewBox="0 0 1000 280" class="curve-svg" role="group" tabindex="0" :aria-label="tr('curve') + '. ' + tr('curveKeyboard')" @mousemove="hoverCurve" @mouseleave="hoveredPoint = null" @keydown="stepCurve" @blur="hoveredPoint = null">
            <g v-for="tick in plot.ticks" :key="tick.y">
              <line x1="76" x2="964" :y1="tick.y" :y2="tick.y" class="curve-grid" />
              <text x="62" :y="tick.y + 4" text-anchor="end" class="curve-label">{{ format(tick.value) }}</text>
            </g>
            <line x1="76" x2="964" :y1="plot.zero" :y2="plot.zero" class="curve-baseline" stroke-dasharray="4 5" />
            <polygon :points="plot.area" fill="var(--curve-dd)" opacity=".07" />
            <polyline :points="plot.drawdown" class="curve-line curve-dd" />
            <polyline :points="plot.line" class="curve-line curve-net" />
            <circle v-if="plot.points.length === 1" :cx="plot.points[0]!.x" :cy="plot.points[0]!.y" r="3" fill="var(--curve-net)" />
            <g v-if="hoveredPoint" class="curve-active">
              <line :x1="hoveredPoint.x" :x2="hoveredPoint.x" y1="24" y2="242" class="curve-baseline" stroke-dasharray="3 4" />
              <circle :cx="hoveredPoint.x" :cy="hoveredPoint.y" r="4" fill="var(--curve-net)" />
              <circle :cx="hoveredPoint.x" :cy="hoveredPoint.dy" r="3" fill="var(--curve-dd)" />
            </g>
            <text v-for="(point, index) in plot.dates" :key="point.x" :x="point.x" y="270" :text-anchor="index === 0 ? 'start' : index === plot.dates.length - 1 ? 'end' : 'middle'" class="curve-label">{{ new Date(point.time_ms).toLocaleDateString(locale, { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' }) }}</text>
          </svg>
          <div v-if="hoveredPoint" class="curve-tooltip" role="status"><span>{{ time(hoveredPoint.time_ms) }}</span><strong>{{ tr('curveCurrent') }} <b class="curve-net-value">{{ format(hoveredPoint.net) }}</b></strong><strong>{{ tr('curveDrawdown') }} <b>{{ format(hoveredPoint.drawdown) }}</b></strong></div>
        </div>
      </div>
    </section>
    <section v-if="tab === 'attribution'" class="space-y-4">
      <div class="card p-3">
        <div class="flex flex-wrap items-center gap-3 mb-3"><label>{{ tr('group') }}<select v-model="groupBy" class="field" :aria-label="tr('group')"><option v-for="d in dimensions" :key="d" :value="d">{{ dimensionLabel(d) }}</option></select></label><p class="text-xs t-faint">{{ tr('modelNote') }}</p></div>
        <div class="overflow-x-auto"><table class="table" :aria-label="tr('group')"><thead><tr><th scope="col">{{ tr('group') }}</th><th scope="col">{{ tr('samples') }}</th><th scope="col">{{ tr('incomplete') }}</th><th scope="col">{{ tr('winRate') }}</th><th scope="col">{{ tr('net') }}</th><th scope="col">{{ tr('pf') }}</th><th scope="col">{{ tr('expectancy') }}</th></tr></thead>
          <tbody><tr v-for="g in groups" :key="g.key"><td class="max-w-60 break-all">{{ g.key }}</td><td>{{ g.sample_count }}</td><td>{{ g.incomplete_count }}</td><td>{{ pct(g.win_rate) }}</td><td :class="color(g.net_pnl)">{{ format(g.net_pnl) }}</td><td>{{ format(g.profit_factor) }}</td><td>{{ format(g.expectancy) }}</td></tr></tbody></table></div>
      </div>
      <div class="card overflow-hidden">
        <BaseEmpty v-if="!trades.length" :text="tr('noData')" />
        <div v-else class="overflow-x-auto"><table class="table" :aria-label="tr('trades')"><thead><tr><th scope="col">{{ tr('inst') }}</th><th scope="col">{{ tr('side') }}</th><th scope="col">{{ tr('closeTime') }}</th><th scope="col">{{ tr('net') }}</th><th scope="col">{{ tr('exitReason') }}</th><th scope="col">{{ tr('evidence') }}</th><th scope="col">{{ tr('details') }}</th></tr></thead>
          <tbody><tr v-for="row in trades" :key="row.id"><td>{{ row.inst }}</td><td>{{ tr(row.side) }}</td><td class="num whitespace-nowrap">{{ row.close_time }}</td><td class="num" :class="color(row.net_pnl)">{{ format(row.net_pnl) }}</td><td>{{ row.exit_reason || tr('unknown') }}</td><td>{{ tr(row.exit_evidence || 'unknown') }}<span v-if="!row.cost_complete" class="block text-xs t-faint">{{ tr('incomplete') }}</span></td><td><button type="button" class="btn btn-ghost btn-sm" @click="inspectTrade(row.id)">{{ tr('details') }}</button></td></tr></tbody></table></div>
        <BasePager :page="tradePage" :page-count="Math.max(1, Math.ceil(tradeTotal / 20))" :total="tradeTotal" @update:page="changePage('trade', $event)" />
      </div>
    </section>

    <section v-if="tab === 'configurations'" class="space-y-4">
      <p class="text-xs t-faint">{{ tr('configurationNote') }}</p>
      <div class="card analysis-compare">
        <label>{{ tr('historical') }}<select v-model="leftId" class="field" :aria-label="tr('historical')"><option value="">{{ tr('selectConfig') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }} · {{ time(c.captured_ms) }}</option></select></label>
        <label>{{ tr('compare') }}<select v-model="rightId" class="field" :aria-label="tr('compare')"><option value="current">{{ tr('current') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }}</option></select></label>
        <button type="button" class="btn btn-ghost" :disabled="!leftId" @click="compare"><GitCompareArrows />{{ tr('compare') }}</button>
      </div>
      <BaseEmpty v-if="!leftId" :text="tr('selectConfig')" />
      <template v-if="leftConfig && rightConfig">
        <div class="card p-3 overflow-x-auto"><h2 class="font-semibold text-sm mb-2">{{ tr('differences') }} · {{ differences.length }}</h2><BaseEmpty v-if="!differences.length" :text="tr('noDifferences')" />
          <table v-else class="table config-diff" :aria-label="tr('differences')"><thead><tr><th scope="col">{{ tr('field') }}</th><th scope="col">{{ tr('before') }}</th><th scope="col">{{ tr('after') }}</th></tr></thead>
            <tbody><tr v-for="d in differences" :key="d.key"><td>{{ d.key }}</td><td><pre tabindex="0">{{ d.before }}</pre></td><td><pre tabindex="0">{{ d.after }}</pre></td></tr></tbody></table>
        </div>
        <div class="grid lg:grid-cols-2 gap-3"><details class="card p-3"><summary>{{ tr('historical') }} · {{ time(leftConfig.captured_ms) }}</summary><BaseCodeBlock :code="json(leftConfig)" max-height="600px" /></details><details class="card p-3"><summary>{{ tr('compare') }} · {{ time(rightConfig.captured_ms) }}</summary><BaseCodeBlock :code="json(rightConfig)" max-height="600px" /></details></div>
      </template>
    </section>
    <section v-if="tab === 'execution'" class="space-y-4">
      <div class="card analysis-stats analysis-stats-summary">
        <BaseStat :label="tr('proposed')" :value="format(execution.proposed_decisions, 0)" />
        <BaseStat :label="tr('passed')" :value="format(execution.passed_decisions, 0)" />
        <BaseStat :label="tr('submitted')" :value="format(execution.submitted_orders, 0)" />
        <BaseStat :label="tr('filled')" :value="format(execution.filled_orders, 0)" />
      </div>
      <p class="text-xs t-faint">{{ tr('executionNote') }}</p>
      <div v-if="execution.rejections?.length" class="card p-3"><h2 class="font-semibold text-sm mb-2">{{ tr('rejection') }}</h2><div v-for="r in execution.rejections" :key="r.reason" class="flex justify-between gap-4 py-1 text-xs"><span>{{ r.reason }}</span><strong class="num">{{ r.count }}</strong></div></div>
      <div class="card overflow-hidden"><BaseEmpty v-if="!events.length" :text="tr('emptyEvents')" />
        <div v-else class="overflow-x-auto"><table class="table" :aria-label="tr('execution')"><thead><tr><th scope="col">{{ tr('time') }}</th><th scope="col">{{ tr('kind') }}</th><th scope="col">{{ tr('inst') }}</th><th scope="col">{{ tr('status') }}</th><th scope="col">{{ tr('evidence') }}</th></tr></thead><tbody><tr v-for="e in events" :key="e.id"><td class="whitespace-nowrap">{{ time(e.occurred_ms) }}</td><td>{{ eventLabel(e.kind) }}<span class="block text-xs t-faint">{{ e.kind }}</span></td><td>{{ e.inst || '—' }}</td><td :class="e.status === 'failed' || e.status === 'rejected' ? 'down' : ''">{{ e.status }}</td><td><button type="button" class="btn btn-ghost btn-sm" @click="inspectEvent(e.id)">{{ tr('raw') }}</button></td></tr></tbody></table></div>
        <BasePager :page="eventPage" :page-count="Math.max(1, Math.ceil(eventTotal / 30))" :total="eventTotal" @update:page="changePage('event', $event)" />
      </div>
    </section>
    <BaseDrawer :open="drawerOpen" width="920px" :title="tradeDetail ? tr('details') + ' · ' + tradeDetail.trade.inst : tr('preview')" @close="drawerOpen = false; detailSequence++">
      <p v-if="detailBusy" role="status" class="t-faint">{{ tr('refresh') }}…</p>
      <div v-if="tradeDetail" class="space-y-3">
        <p v-if="!tradeDetail.trade.decision_id" class="text-xs t-faint">{{ tr('detailsUnknown') }}</p>
        <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
          <BaseStat :label="tr('net')" :value="format(tradeDetail.trade.net_pnl)" />
          <BaseStat :label="tr('fees')" :value="format(tradeDetail.trade.fee)" />
          <BaseStat :label="tr('funding')" :value="format(tradeDetail.trade.funding_fee)" />
        </div>
        <dl class="grid grid-cols-2 gap-3 text-xs card p-3">
          <div><dt class="t-faint">{{ tr('openTime') }}</dt><dd class="mt-1">{{ tradeDetail.trade.open_time }}</dd></div>
          <div><dt class="t-faint">{{ tr('closeTime') }}</dt><dd class="mt-1">{{ tradeDetail.trade.close_time }}</dd></div>
          <div><dt class="t-faint">{{ t('dash.ledger.col.entry') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.open_px, 4) }}</dd></div>
          <div><dt class="t-faint">{{ t('dash.ledger.col.exit') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.close_px, 4) }}</dd></div>
          <div><dt class="t-faint">{{ tr('exitReason') }}</dt><dd class="mt-1">{{ tradeDetail.trade.exit_reason || tr('unknown') }} · {{ tr(tradeDetail.trade.exit_evidence || 'unknown') }}</dd></div>
          <div><dt class="t-faint">{{ tr('delta') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.entry_deviation_bps) }}</dd></div>
        </dl>
        <details class="card p-3"><summary>{{ tr('body') }}</summary><BaseCodeBlock :code="json(tradeDetail.trade)" /></details>
        <h3 class="text-sm font-semibold">{{ tr('timeline') }}</h3>
        <div v-for="e in tradeDetail.events" :key="e.id" class="flex items-center gap-2 text-xs border-b py-2" style="border-color: var(--line-1)"><span class="num">{{ time(e.occurred_ms) }}</span><span class="flex-1">{{ eventLabel(e.kind) }} · {{ e.status }}</span><button type="button" class="btn btn-ghost btn-sm" @click="inspectEvent(e.id, true)">{{ tr('raw') }}</button></div>
      </div>
      <div v-if="evidence" class="space-y-2 mt-4"><h3 class="text-sm font-semibold">{{ evidence.kind }}</h3><BaseCodeBlock :code="json(evidence)" max-height="70vh" /></div>
    </BaseDrawer>
  </div>
</template>
<style scoped>
.analysis-page { color: var(--ink-1); min-width: 0; }
.analysis-page label { display: flex; flex-direction: column; min-width: 0; gap: 6px; font-size: var(--text-3xs); color: var(--ink-2); }
.analysis-filters {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: end;
  gap: 12px 16px;
  padding: 16px;
}
.analysis-account { grid-column: span 2; }
.analysis-filters .field, .analysis-compare .field { min-width: 0; max-width: 100%; }
.analysis-apply { width: 100%; }
.analysis-compare {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: end;
  gap: 12px 16px;
  padding: 16px;
}
.analysis-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 1200px) {
  .analysis-filters { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .analysis-configuration { grid-column: span 3; }
  .analysis-apply { grid-column: span 2; }
  .analysis-stats-summary { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .analysis-stats-metrics { grid-template-columns: repeat(5, minmax(0, 1fr)); }
}
@media (max-width: 1099px) {
  .analysis-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 12px; }
  .analysis-outcome, .analysis-configuration, .analysis-apply { grid-column: span 2; }
  .analysis-compare { grid-template-columns: minmax(0, 1fr); padding: 12px; }
}
@media (max-width: 479px) {
  .analysis-stats { grid-template-columns: minmax(0, 1fr); }
}
.analysis-page summary { cursor: pointer; font-size: var(--text-xs); padding: 6px 0; }
.config-diff { table-layout: fixed; min-width: 660px; }
.config-diff td { overflow-wrap: anywhere; vertical-align: top; }
.config-diff pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow: auto; font-size: var(--text-3xs); }
.curve-wrap { --curve-net: #22a995; --curve-dd: #d47b78; position: relative; }
.curve-summary span { display: flex; flex-direction: column; gap: 8px; padding: 12px 16px; background: var(--surface-2); border: 1px solid var(--line-1); border-radius: var(--r-card); }
.curve-summary b { color: var(--ink-3); font-size: var(--text-3xs); font-weight: 500; }
.curve-summary strong { color: var(--ink-1); font-size: var(--text-lg); font-weight: 600; font-variant-numeric: tabular-nums; }
.curve-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; padding: 12px 6px 4px; font-size: var(--text-3xs); color: var(--ink-3); }
.curve-legend span { display: inline-flex; align-items: center; gap: 6px; }
.curve-legend i { width: 16px; height: 3px; border-radius: var(--r-xs); }
.curve-key-net { background: var(--curve-net); }
.curve-key-dd { background: var(--curve-dd); }
.curve-unit { margin-left: auto; }
.curve-svg { display: block; width: 100%; height: clamp(240px, 26vw, 360px); cursor: crosshair; }
.curve-svg:focus-visible { outline: 2px solid var(--curve-net); outline-offset: 3px; border-radius: var(--r-ctl); }
.curve-label { fill: var(--ink-3); font-size: var(--text-3xs); font-variant-numeric: tabular-nums; }
.curve-grid { stroke: var(--line-1); stroke-width: 1; }
.curve-baseline { stroke: var(--ink-3); opacity: .4; }
.curve-line { fill: none; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.curve-net { stroke: var(--curve-net); stroke-width: 2.2; }
.curve-dd { stroke: var(--curve-dd); stroke-width: 1.5; opacity: .85; }
.curve-active { pointer-events: none; }
.curve-active circle { stroke: var(--surface-2); stroke-width: 2; }
.curve-tooltip { position: absolute; top: 42px; right: 12px; display: grid; gap: 8px; max-width: calc(100% - 24px); padding: 12px 16px; border: 1px solid var(--line-1); border-radius: var(--r-card); background: var(--surface-2); box-shadow: 0 8px 24px #00000010; color: var(--ink-3); font-size: var(--text-3xs); pointer-events: none; }
.curve-tooltip strong { display: flex; justify-content: space-between; gap: 20px; font-weight: 400; }
.curve-tooltip b { color: var(--curve-dd); font-variant-numeric: tabular-nums; }
.curve-tooltip .curve-net-value { color: var(--curve-net); }
</style>
